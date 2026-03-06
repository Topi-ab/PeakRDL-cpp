// Copyright (c) 2026 PeakRDL-cpp contributors
// SPDX-License-Identifier: LGPL-3.0-or-later

#include <cstdint>
#include <iostream>
#include <unordered_map>

#include "demo_regs.hpp"

struct MockBus {
    std::unordered_map<std::uint32_t, std::uint32_t> mem;

    std::uint32_t read(std::uint32_t addr) {
        return mem[addr];
    }

    void write(std::uint32_t addr, std::uint32_t value) {
        mem[addr] = value;
    }
};

int main() {
    MockBus bus;
    demo::DemoRoot<MockBus> my_root(bus, 0);

    // Direct HW read-modify-write on field.
    my_root.regfile_1[0].sub_regfile.example_reg.enable.write(1u);

    // Shadow update only. No HW write yet.
    my_root.regfile_1[0].sub_regfile.example_reg.mode.shadow.write(3u);
    std::cout << "mode shadow = "
              << my_root.regfile_1[0].sub_regfile.example_reg.mode.shadow.read()
              << "\n";

    // Flush all dirty regs in this regfile element.
    my_root.regfile_1[0].shadow.flush();

    // Hardware read updates shadow for all fields in the register.
    std::uint64_t mode_hw = my_root.regfile_1[0].sub_regfile.example_reg.mode.read();
    std::cout << "mode hw = " << mode_hw << "\n";

    return 0;
}
