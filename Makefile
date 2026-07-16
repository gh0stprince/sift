.PHONY: prepush lint test smoke

lint:
	pylint sift/ tests/

test:
	pytest -m "not integration"

smoke:
	python -m sift --help >/dev/null

prepush: lint test smoke
