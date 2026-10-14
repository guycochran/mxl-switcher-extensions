.PHONY: test

test:
	python3 scripts/validate.py
	python3 -m unittest discover -s tests -v
