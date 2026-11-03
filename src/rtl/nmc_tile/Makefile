PYTHON ?= python3
OUTPUT ?= ../../../dump/rtl_tile
SEEDS ?= 8

.PHONY: test test-root lint
test:
	$(PYTHON) ../../../tools/rtl/run_tile_rtl.py --seeds $(SEEDS) --output-dir $(OUTPUT)

test-root:
	$(PYTHON) ../../../tools/rtl/run_root_writeback_rtl.py --seeds $(SEEDS) --output-dir $(OUTPUT)/root_writeback

lint:
	verilator --lint-only -Wall -Wno-UNUSEDSIGNAL --top-module nmc_tile_compute \
		nmc_mac4.sv nmc_tile_sram.sv nmc_tile_compute.sv
