PYTHON ?= python3
OUTPUT ?= ../../../dump/rtl_tile
SEEDS ?= 8

.PHONY: test lint
test:
	$(PYTHON) ../../../tools/rtl/run_tile_rtl.py --seeds $(SEEDS) --output-dir $(OUTPUT)

lint:
	verilator --lint-only -Wall -Wno-UNUSEDSIGNAL --top-module nmc_tile_compute \
		nmc_mac4.sv nmc_tile_sram.sv nmc_tile_compute.sv
