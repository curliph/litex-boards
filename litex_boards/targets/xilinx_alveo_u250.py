#!/usr/bin/env python3

#
# This file is part of LiteX-Boards.
#
# Copyright (c) 2020 Fei Gao <feig@princeton.edu>
# Copyright (c) 2020 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2020 David Shah <dave@ds0.me>
# SPDX-License-Identifier: BSD-2-Clause

import argparse, os

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex_boards.platforms import alveo_u250

from litex.soc.cores.clock import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *

from litex.soc.cores.led import LedChaser
from litedram.modules import MTA18ASF2G72PZ
from litedram.phy import usddrphy

from litepcie.phy.usppciephy import USPPCIEPHY
from litepcie.software import generate_litepcie_software

# CRG ----------------------------------------------------------------------------------------------

class _CRG(Module):
    def __init__(self, platform, sys_clk_freq):
        self.rst = Signal()
        self.clock_domains.cd_sys    = ClockDomain()
        self.clock_domains.cd_sys4x  = ClockDomain(reset_less=True)
        self.clock_domains.cd_pll4x  = ClockDomain(reset_less=True)
        self.clock_domains.cd_idelay = ClockDomain()

        # # #

        self.submodules.pll = pll = USMMCM(speedgrade=-2)
        self.comb += pll.reset.eq(self.rst)
        pll.register_clkin(platform.request("clk300", 0), 300e6)
        pll.create_clkout(self.cd_pll4x, sys_clk_freq*4, buf=None, with_reset=False)
        pll.create_clkout(self.cd_idelay, 500e6)
        platform.add_false_path_constraints(self.cd_sys.clk, pll.clkin) # Ignore sys_clk to pll.clkin path created by SoC's rst.

        self.specials += [
            Instance("BUFGCE_DIV", name="main_bufgce_div",
                p_BUFGCE_DIVIDE=4,
                i_CE=1, i_I=self.cd_pll4x.clk, o_O=self.cd_sys.clk),
            Instance("BUFGCE", name="main_bufgce",
                i_CE=1, i_I=self.cd_pll4x.clk, o_O=self.cd_sys4x.clk),
        ]

        self.submodules.idelayctrl = USIDELAYCTRL(cd_ref=self.cd_idelay, cd_sys=self.cd_sys)

# BaseSoC ------------------------------------------------------------------------------------------

class BaseSoC(SoCCore):
    def __init__(self, sys_clk_freq=int(125e6), with_led_chaser=True, with_pcie=False, **kwargs):
        platform = alveo_u250.Platform()

        # SoCCore ----------------------------------------------------------------------------------
        SoCCore.__init__(self, platform, sys_clk_freq,
            ident = "LiteX SoC on Alveo U250",
            **kwargs)

        # CRG --------------------------------------------------------------------------------------
        self.submodules.crg = _CRG(platform, sys_clk_freq)

        # DDR4 SDRAM -------------------------------------------------------------------------------
        if not self.integrated_main_ram_size:
            self.submodules.ddrphy = usddrphy.USPDDRPHY(platform.request("ddram"),
                memtype          = "DDR4",
                sys_clk_freq     = sys_clk_freq,
                iodelay_clk_freq = 500e6,
                is_rdimm         = True)
            self.add_sdram("sdram",
                phy           = self.ddrphy,
                module        = MTA18ASF2G72PZ(sys_clk_freq, "1:4"),
                size          = 0x40000000,
                l2_cache_size = kwargs.get("l2_size", 8192)
            )

        # Firmware RAM (To ease initial LiteDRAM calibration support) ------------------------------
        self.add_ram("firmware_ram", 0x20000000, 0x8000)

        # PCIe -------------------------------------------------------------------------------------
        if with_pcie:
            self.submodules.pcie_phy = USPPCIEPHY(platform, platform.request("pcie_x4"),
                data_width = 128,
                bar0_size  = 0x20000)
            self.add_pcie(phy=self.pcie_phy, ndmas=1)

        # Leds -------------------------------------------------------------------------------------
        if with_led_chaser:
            self.submodules.leds = LedChaser(
                pads         = platform.request_all("user_led"),
                sys_clk_freq = sys_clk_freq)

# Build --------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteX SoC on Alveo U250")
    parser.add_argument("--build",        action="store_true", help="Build bitstream.")
    parser.add_argument("--load",         action="store_true", help="Load bitstream.")
    parser.add_argument("--sys-clk-freq", default=125e6,       help="System clock frequency.")
    parser.add_argument("--with-pcie",    action="store_true", help="Enable PCIe support.")
    parser.add_argument("--driver",       action="store_true", help="Generate PCIe driver.")
    builder_args(parser)
    soc_core_args(parser)
    args = parser.parse_args()

    soc = BaseSoC(
        sys_clk_freq = int(float(args.sys_clk_freq)),
        with_pcie    = args.with_pcie,
        **soc_core_argdict(args)
    )
    builder = Builder(soc, **builder_argdict(args))
    builder.build(run=args.build)

    if args.driver:
        generate_litepcie_software(soc, os.path.join(builder.output_dir, "driver"))

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(builder.get_bitstream_filename(mode="sram"))

if __name__ == "__main__":
    main()
