# Dragged Out — build system
# Usage: make build

.PHONY: build serve clean

build:
	.venv/bin/python generator.py

serve:
	.venv/bin/python generator.py --serve

clean:
	rm -rf build/*