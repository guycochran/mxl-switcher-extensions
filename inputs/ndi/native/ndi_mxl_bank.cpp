// SPDX-FileCopyrightText: 2026 Contributors to mxl-switcher-extensions
// SPDX-License-Identifier: Apache-2.0

#include <gst/app/gstappsink.h>
#include <gst/gst.h>
#include <sys/resource.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

// The runtime image intentionally contains libmxl but not its development
// headers. Keep this narrow ABI declaration synchronized with the public MXL
// 1.x C API. No private SDK implementation is copied into this repository.
extern "C" {
struct mxlInstance_t;
struct mxlFlowWriter_t;
using mxlInstance = mxlInstance_t*;
using mxlFlowWriter = mxlFlowWriter_t*;
enum mxlStatus : int { MXL_STATUS_OK = 0 };
struct mxlRational {
  std::int64_t numerator;
  std::int64_t denominator;
};
struct mxlGrainInfo {
  std::uint32_t version;
  std::uint32_t size;
  std::uint64_t index;
  std::uint32_t flags;
  std::uint32_t grainSize;
  std::uint16_t totalSlices;
  std::uint16_t validSlices;
  std::uint8_t reserved[4068];
};
mxlInstance mxlCreateInstance(char const*, char const*);
mxlStatus mxlDestroyInstance(mxlInstance);
mxlStatus mxlCreateFlowWriter(mxlInstance, char const*, char const*,
                              mxlFlowWriter*, void*, bool*);
mxlStatus mxlReleaseFlowWriter(mxlInstance, mxlFlowWriter);
mxlStatus mxlFlowWriterOpenGrain(mxlFlowWriter, std::uint64_t, mxlGrainInfo*,
                                 std::uint8_t**);
mxlStatus mxlFlowWriterCancelGrain(mxlFlowWriter);
mxlStatus mxlFlowWriterCommitGrain(mxlFlowWriter, mxlGrainInfo const*);
std::uint64_t mxlGetCurrentIndex(mxlRational const*);
std::uint64_t mxlGetNsUntilIndex(std::uint64_t, mxlRational const*);
void mxlSleepForNs(std::uint64_t);
}

static_assert(sizeof(mxlGrainInfo) == 4096,
              "mxlGrainInfo must match the public MXL 1.x ABI");

namespace {
std::atomic_bool running{true};
void stop(int) { running = false; }

std::runtime_error mxl_error(std::string_view operation, mxlStatus status) {
  return std::runtime_error(std::string(operation) + " failed with MXL status " +
                            std::to_string(static_cast<int>(status)));
}

std::string required_env(const std::string& name) {
  const char* value = std::getenv(name.c_str());
  if (value == nullptr || *value == '\0') {
    throw std::invalid_argument("missing required environment value " + name);
  }
  return value;
}

std::string gst_quote(std::string_view value) {
  std::string result{"\""};
  for (char ch : value) {
    if (ch == '\\' || ch == '"') result.push_back('\\');
    result.push_back(ch);
  }
  result.push_back('"');
  return result;
}

std::unordered_map<std::string, std::string> parse_args(int argc, char** argv) {
  std::unordered_map<std::string, std::string> args;
  for (int i = 1; i < argc; i += 2) {
    if (i + 1 >= argc || std::string_view(argv[i]).find("--") != 0) {
      throw std::invalid_argument("arguments must be --name value pairs");
    }
    args.emplace(std::string(argv[i] + 2), argv[i + 1]);
  }
  return args;
}

const std::string& required(
    const std::unordered_map<std::string, std::string>& args,
    std::string_view name) {
  const auto found = args.find(std::string(name));
  if (found == args.end() || found->second.empty()) {
    throw std::invalid_argument("missing --" + std::string(name));
  }
  return found->second;
}

std::string flow_definition(const std::string& id, std::size_t ordinal,
                            int width, int height, int rate_num,
                            int rate_den) {
  const auto label = "NDI Input Bank Video " + std::to_string(ordinal + 1);
  return "{\"description\":\"" + label +
         "\",\"id\":\"" + id +
         "\",\"tags\":{\"urn:x-nmos:tag:grouphint/v1.0\":[\"NDI Input " +
         "Bank:Video " + std::to_string(ordinal + 1) +
         "\"]},\"format\":\"urn:x-nmos:format:video\",\"label\":\"" +
         label +
         "\",\"parents\":[],\"media_type\":\"video/v210\","
         "\"grain_rate\":{\"numerator\":" + std::to_string(rate_num) +
         ",\"denominator\":" + std::to_string(rate_den) +
         "},\"frame_width\":" + std::to_string(width) +
         ",\"frame_height\":" + std::to_string(height) +
         ",\"interlace_mode\":\"progressive\",\"colorspace\":\"BT709\","
         "\"components\":[{\"name\":\"Y\",\"width\":" +
         std::to_string(width) + ",\"height\":" + std::to_string(height) +
         ",\"bit_depth\":10},{\"name\":\"Cb\",\"width\":" +
         std::to_string(width / 2) + ",\"height\":" +
         std::to_string(height) +
         ",\"bit_depth\":10},{\"name\":\"Cr\",\"width\":" +
         std::to_string(width / 2) + ",\"height\":" +
         std::to_string(height) + ",\"bit_depth\":10}]}";
}

using Clock = std::chrono::steady_clock;

std::int64_t elapsed_ns(Clock::time_point start, Clock::time_point end) {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count();
}

// Optional, secret-free scheduler telemetry. It records only durations and
// counters for the coordinator thread: never selectors, flow IDs, or payloads.
// The stages separate a late wake (scheduler or CPU contention), NDI handoff
// (`update`), buffer mapping, MXL grain open, payload copy, and commit.
class TickTiming {
 public:
  explicit TickTiming(std::int64_t report_seconds) : report_seconds_(report_seconds) {
    reset(Clock::now());
  }
  bool enabled() const { return report_seconds_ > 0; }

  void wake(std::int64_t late_ns, std::uint64_t frames_behind) {
    ++ticks_;
    if (frames_behind > 0) ++catchup_ticks_;
    max_frames_behind_ = std::max(max_frames_behind_, frames_behind);
    max_wake_late_ns_ = std::max(max_wake_late_ns_, late_ns);
    std::size_t bucket = 0;
    while (bucket < kLateBucketsMs.size() && late_ns >= kLateBucketsMs[bucket] * 1'000'000) {
      ++bucket;
    }
    ++late_histogram_[bucket];
  }
  void stage(std::int64_t& maximum, std::int64_t value) { maximum = std::max(maximum, value); }
  void tick_done(std::int64_t work_ns) {
    max_tick_work_ns_ = std::max(max_tick_work_ns_, work_ns);
    total_tick_work_ns_ += work_ns;
  }

  std::int64_t max_update_ns{0}, max_map_ns{0}, max_open_ns{0}, max_copy_ns{0}, max_commit_ns{0};

  void maybe_report(std::uint64_t source_holds) {
    const auto now = Clock::now();
    const auto interval = elapsed_ns(started_, now);
    if (!enabled() || interval < report_seconds_ * 1'000'000'000) return;
    rusage usage{};
#ifdef RUSAGE_THREAD
    getrusage(RUSAGE_THREAD, &usage);
#else
    getrusage(RUSAGE_SELF, &usage);
#endif
    std::cout << "{\"event\":\"ndi_bank_timing\",\"interval_s\":" << interval / 1'000'000'000.0
              << ",\"ticks\":" << ticks_ << ",\"catchup_ticks\":" << catchup_ticks_
              << ",\"max_frames_behind\":" << max_frames_behind_
              << ",\"max_wake_late_us\":" << max_wake_late_ns_ / 1000
              << ",\"wake_late_ms_histogram\":{";
    for (std::size_t i = 0; i < late_histogram_.size(); ++i) {
      std::cout << (i ? "," : "") << "\""
                << (i < kLateBucketsMs.size() ? "lt_" + std::to_string(kLateBucketsMs[i])
                                              : "ge_" + std::to_string(kLateBucketsMs.back()))
                << "\":" << late_histogram_[i];
    }
    std::cout << "},\"max_update_us\":" << max_update_ns / 1000
              << ",\"max_map_us\":" << max_map_ns / 1000
              << ",\"max_open_us\":" << max_open_ns / 1000
              << ",\"max_copy_us\":" << max_copy_ns / 1000
              << ",\"max_commit_us\":" << max_commit_ns / 1000
              << ",\"max_tick_work_us\":" << max_tick_work_ns_ / 1000
              << ",\"mean_tick_work_us\":" << (ticks_ ? total_tick_work_ns_ / ticks_ / 1000 : 0)
              << ",\"source_holds\":" << source_holds - holds_at_start_
              << ",\"scheduler_involuntary_switches\":" << usage.ru_nivcsw - nivcsw_at_start_
              << ",\"scheduler_voluntary_switches\":" << usage.ru_nvcsw - nvcsw_at_start_
              << ",\"scheduler_minor_faults\":" << usage.ru_minflt - minflt_at_start_
              << ",\"scheduler_major_faults\":" << usage.ru_majflt - majflt_at_start_
              << "}" << std::endl;
    holds_at_start_ = source_holds;
    nivcsw_at_start_ = usage.ru_nivcsw;
    nvcsw_at_start_ = usage.ru_nvcsw;
    minflt_at_start_ = usage.ru_minflt;
    majflt_at_start_ = usage.ru_majflt;
    reset(now);
  }

 private:
  static constexpr std::array<std::int64_t, 5> kLateBucketsMs{1, 5, 17, 34, 100};

  void reset(Clock::time_point now) {
    started_ = now;
    ticks_ = catchup_ticks_ = max_frames_behind_ = 0;
    max_wake_late_ns_ = max_tick_work_ns_ = total_tick_work_ns_ = 0;
    max_update_ns = max_map_ns = max_open_ns = max_copy_ns = max_commit_ns = 0;
    late_histogram_.fill(0);
  }

  std::int64_t report_seconds_;
  Clock::time_point started_{};
  std::uint64_t ticks_{0}, catchup_ticks_{0}, max_frames_behind_{0};
  std::int64_t max_wake_late_ns_{0}, max_tick_work_ns_{0}, total_tick_work_ns_{0};
  std::array<std::uint64_t, kLateBucketsMs.size() + 1> late_histogram_{};
  std::uint64_t holds_at_start_{0};
  long nivcsw_at_start_{0}, nvcsw_at_start_{0}, minflt_at_start_{0}, majflt_at_start_{0};
};

class Writer {
 public:
  Writer(mxlInstance instance, const std::string& definition)
      : instance_(instance) {
    bool created = false;
    const auto status = mxlCreateFlowWriter(instance_, definition.c_str(),
                                             nullptr, &handle_, nullptr,
                                             &created);
    if (status != MXL_STATUS_OK) throw mxl_error("mxlCreateFlowWriter", status);
    if (!created) {
      mxlReleaseFlowWriter(instance_, handle_);
      handle_ = nullptr;
      throw std::runtime_error("output flow already has a writer");
    }
  }
  ~Writer() {
    if (handle_ != nullptr) mxlReleaseFlowWriter(instance_, handle_);
  }
  Writer(const Writer&) = delete;
  Writer& operator=(const Writer&) = delete;

  void write(std::uint64_t index, GstBuffer* buffer, TickTiming& timing) {
    auto mark = Clock::now();
    const auto lap = [&](std::int64_t& maximum) {
      const auto now = Clock::now();
      timing.stage(maximum, elapsed_ns(mark, now));
      mark = now;
    };
    GstMapInfo map{};
    if (!gst_buffer_map(buffer, &map, GST_MAP_READ)) {
      throw std::runtime_error("could not map decoded v210 frame");
    }
    lap(timing.max_map_ns);
    mxlGrainInfo info{};
    std::uint8_t* payload = nullptr;
    auto status = mxlFlowWriterOpenGrain(handle_, index, &info, &payload);
    lap(timing.max_open_ns);
    if (status != MXL_STATUS_OK) {
      gst_buffer_unmap(buffer, &map);
      throw mxl_error("mxlFlowWriterOpenGrain", status);
    }
    if (payload == nullptr || map.size < info.grainSize) {
      mxlFlowWriterCancelGrain(handle_);
      gst_buffer_unmap(buffer, &map);
      throw std::runtime_error("decoded frame is smaller than the MXL grain");
    }
    std::memcpy(payload, map.data, info.grainSize);
    gst_buffer_unmap(buffer, &map);
    lap(timing.max_copy_ns);
    info.flags = 0;
    info.validSlices = info.totalSlices;
    status = mxlFlowWriterCommitGrain(handle_, &info);
    lap(timing.max_commit_ns);
    if (status != MXL_STATUS_OK) {
      mxlFlowWriterCancelGrain(handle_);
      throw mxl_error("mxlFlowWriterCommitGrain", status);
    }
  }

 private:
  mxlInstance instance_{nullptr};
  mxlFlowWriter handle_{nullptr};
};

class Input {
 public:
  Input(std::size_t ordinal, int width, int height, int rate_num, int rate_den,
        int convert_threads, const std::string& timestamp_mode) {
    const auto prefix = "NDI_BANK_SOURCE_" + std::to_string(ordinal);
    const auto type = required_env(prefix + "_TYPE");
    const auto value = required_env(prefix + "_VALUE");
    std::string selector;
    if (type == "name") selector = "ndi-name=" + gst_quote(value);
    else if (type == "address") selector = "url-address=" + gst_quote(value);
    else throw std::invalid_argument("invalid source selector type");

    const auto pipeline =
        "ndisrc " + selector + " timestamp-mode=" + timestamp_mode +
        " ! ndisrcdemux name=d "
        "d.video ! queue max-size-buffers=12 max-size-time=0 max-size-bytes=0 "
        "leaky=downstream ! watchdog timeout=30000 ! videorate ! "
        "video/x-raw,framerate=" + std::to_string(rate_num) + "/" +
        std::to_string(rate_den) + " ! videoscale add-borders=true ! "
        "video/x-raw,width=" + std::to_string(width) + ",height=" +
        std::to_string(height) +
        ",pixel-aspect-ratio=1/1 ! videoconvert n-threads=" +
        std::to_string(convert_threads) + " ! video/x-raw,format=v210,width=" +
        std::to_string(width) + ",height=" + std::to_string(height) +
        ",pixel-aspect-ratio=1/1,framerate=" + std::to_string(rate_num) + "/" +
        std::to_string(rate_den) +
        ",interlace-mode=progressive,colorimetry=bt709 ! "
        "appsink name=bank_sink sync=false max-buffers=2 drop=true";
    GError* error = nullptr;
    pipeline_ = gst_parse_launch(pipeline.c_str(), &error);
    if (pipeline_ == nullptr || error != nullptr) {
      const std::string message = error == nullptr ? "unknown error" : error->message;
      if (error != nullptr) g_error_free(error);
      throw std::runtime_error("could not construct NDI receiver: " + message);
    }
    auto* element = gst_bin_get_by_name(GST_BIN(pipeline_), "bank_sink");
    if (element == nullptr) throw std::runtime_error("appsink is unavailable");
    sink_ = GST_APP_SINK(element);
    bus_ = gst_element_get_bus(pipeline_);
    if (gst_element_set_state(pipeline_, GST_STATE_PLAYING) ==
        GST_STATE_CHANGE_FAILURE) {
      throw std::runtime_error("NDI receiver could not enter PLAYING");
    }
  }
  ~Input() {
    if (pipeline_ != nullptr) gst_element_set_state(pipeline_, GST_STATE_NULL);
    if (latest_ != nullptr) gst_buffer_unref(latest_);
    if (bus_ != nullptr) gst_object_unref(bus_);
    if (sink_ != nullptr) gst_object_unref(sink_);
    if (pipeline_ != nullptr) gst_object_unref(pipeline_);
  }
  Input(const Input&) = delete;
  Input& operator=(const Input&) = delete;

  void check_bus() {
    while (auto* message = gst_bus_pop_filtered(
               bus_, static_cast<GstMessageType>(GST_MESSAGE_ERROR |
                                                 GST_MESSAGE_EOS))) {
      if (GST_MESSAGE_TYPE(message) == GST_MESSAGE_ERROR) {
        GError* error = nullptr;
        gchar* debug = nullptr;
        gst_message_parse_error(message, &error, &debug);
        const std::string text = error == nullptr ? "unknown media error" : error->message;
        if (error != nullptr) g_error_free(error);
        g_free(debug);  // Debug may contain a private source selector; never print it.
        gst_message_unref(message);
        throw std::runtime_error("NDI receiver failed: " + text);
      }
      gst_message_unref(message);
      throw std::runtime_error("NDI receiver reached end of stream");
    }
  }

  bool update(GstClockTime timeout = 0) {
    bool changed = false;
    GstSample* sample = gst_app_sink_try_pull_sample(sink_, timeout);
    while (sample != nullptr) {
      GstBuffer* buffer = gst_sample_get_buffer(sample);
      if (buffer != nullptr) {
        GstBuffer* replacement = gst_buffer_ref(buffer);
        if (latest_ != nullptr) gst_buffer_unref(latest_);
        latest_ = replacement;
        changed = true;
      }
      gst_sample_unref(sample);
      sample = gst_app_sink_try_pull_sample(sink_, 0);
    }
    return changed;
  }

  GstBuffer* latest() const { return latest_; }

 private:
  GstElement* pipeline_{nullptr};
  GstAppSink* sink_{nullptr};
  GstBus* bus_{nullptr};
  GstBuffer* latest_{nullptr};
};
}  // namespace

int main(int argc, char** argv) {
  mxlInstance instance = nullptr;
  try {
    gst_init(&argc, &argv);
    const auto args = parse_args(argc, argv);
    const auto& domain = required(args, "domain");
    const auto count = std::stoul(required(args, "source-count"));
    const auto width = std::stoi(required(args, "width"));
    const auto height = std::stoi(required(args, "height"));
    const auto rate_num = std::stoi(required(args, "rate-numerator"));
    const auto rate_den = std::stoi(required(args, "rate-denominator"));
    const auto convert_threads = std::stoi(required(args, "convert-threads"));
    const auto& timestamp_mode = required(args, "timestamp-mode");
    // Optional and off by default, so existing command lines are unchanged.
    const auto report = args.find("timing-report-seconds");
    const auto report_seconds = report == args.end() ? 0 : std::stoll(report->second);
    if (report_seconds < 0 || report_seconds > 3600) {
      throw std::invalid_argument("timing report interval must be 0-3600 seconds");
    }
    if (count < 2 || count > 8 || width <= 0 || height <= 0 || rate_num <= 0 ||
        rate_den <= 0 || convert_threads < 1 || convert_threads > 32) {
      throw std::invalid_argument("invalid bank geometry, rate, or source count");
    }

    instance = mxlCreateInstance(domain.c_str(), nullptr);
    if (instance == nullptr) throw std::runtime_error("could not open MXL domain");
    std::vector<std::unique_ptr<Input>> inputs;
    std::vector<std::unique_ptr<Writer>> writers;
    for (std::size_t i = 0; i < count; ++i) {
      inputs.emplace_back(std::make_unique<Input>(i, width, height, rate_num,
                                                  rate_den, convert_threads,
                                                  timestamp_mode));
      writers.emplace_back(std::make_unique<Writer>(
          instance,
          flow_definition(required_env("NDI_BANK_SOURCE_" + std::to_string(i) +
                                       "_FLOW"),
                          i, width, height, rate_num, rate_den)));
    }

    // Preroll every source before publishing any flow. A bank never presents a
    // partially initialized switching surface.
    for (auto& input : inputs) {
      while (running && input->latest() == nullptr) {
        input->check_bus();
        input->update(250 * GST_MSECOND);
      }
    }
    if (!running) throw std::runtime_error("stopped during preroll");

    std::signal(SIGINT, stop);
    std::signal(SIGTERM, stop);
    const mxlRational rate{rate_num, rate_den};
    auto index = mxlGetCurrentIndex(&rate) + 2;
    std::uint64_t ticks = 0;
    std::uint64_t repeated = 0;
    TickTiming timing(report_seconds);
    while (running) {
      const auto until = mxlGetNsUntilIndex(index, &rate);
      const auto deadline = Clock::now() + std::chrono::nanoseconds(until);
      mxlSleepForNs(until);
      const auto woke = Clock::now();
      if (timing.enabled()) {
        // A zero wait means the index was already due: count how far behind
        // the MXL clock the coordinator is rather than inventing a lateness.
        const auto current = mxlGetCurrentIndex(&rate);
        timing.wake(std::max<std::int64_t>(0, elapsed_ns(deadline, woke)),
                    current > index ? current - index : 0);
      }
      for (auto& input : inputs) {
        input->check_bus();
        if (!input->update()) ++repeated;
      }
      if (timing.enabled()) timing.stage(timing.max_update_ns, elapsed_ns(woke, Clock::now()));
      // One coordinator owns every writer. Each tick is committed to the same
      // explicit MXL index for all flows before the bank advances.
      for (std::size_t i = 0; i < count; ++i) {
        writers[i]->write(index, inputs[i]->latest(), timing);
      }
      if (timing.enabled()) {
        timing.tick_done(elapsed_ns(woke, Clock::now()));
        timing.maybe_report(repeated);
      }
      ++index;
      ++ticks;
    }
    std::cout << "native_bank_ticks=" << ticks << " outputs=" << ticks * count
              << " source_holds=" << repeated << '\n';
    writers.clear();
    inputs.clear();
    mxlDestroyInstance(instance);
    return 0;
  } catch (const std::exception& error) {
    if (instance != nullptr) mxlDestroyInstance(instance);
    std::cerr << "ndi-mxl-bank: " << error.what() << '\n';
    return 2;
  }
}
