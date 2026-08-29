# Quantum Optics Home Lab Components

Documented 2026-07-27 based on research session for DIY quantum optics laboratory setup.

## Source References

- **EduQ V1** (GitHub: yallain/EduQ-V1): Open-source DIY quantum optics platform
- **Thorlabs**: Single photon detectors and optical components
- **Edmund Optics**: Optical tables and breadboards
- **Entangled Future**: Quantum hardware components directory

## Core Component Categories

### 1. Light Sources

| Component | Specification | Cost Range | Purpose |
|-----------|---------------|------------|---------|
| Laser diode | 405 nm (UV-A), 10-100 mW | $150-$500 | Pump source for SPDC |
| Tunable laser | Variable wavelength | $500-$2000 | Advanced experiments |

### 2. Nonlinear Crystals

| Component | Specification | Cost Range | Purpose |
|-----------|---------------|------------|---------|
| BBO crystal | Beta Barium Borate, cut angle | $100-$300 | SPDC (Spontaneous Parametric Down-Conversion) |
| KTP crystal | Potassium Titanyl Phosphate | $150-$400 | Alternative nonlinear medium |

### 3. Polarization Control

| Component | Specification | Cost Range | Purpose |
|-----------|---------------|------------|---------|
| PBS | Polarizing Beam Splitter, 50:50 | $50-$200 | Separate orthogonal polarizations |
| HWP | Half-wave plate, various angles | $30-$150 | Rotate polarization |
| QWP | Quarter-wave plate | $30-$150 | Convert linear to circular |

### 4. Photon Detection

| Component | Specification | Cost Range | Purpose |
|-----------|---------------|------------|---------|
| SPAD | Single Photon Avalanche Diode | $200-$800 | Single photon detection |
| APD | Avalanche Photodiode | $300-$1500 | High-efficiency detection |
| Coincidence module | FPGA-based, <100 ps timing | $400-$2000 | Measure photon correlations |

### 5. Optical Infrastructure

| Component | Specification | Cost Range | Purpose |
|-----------|---------------|------------|---------|
| Optical breadboard | Aluminum, 1m x 0.5m | $200-$800 | Stable optical path |
| Kinematic mounts | Standard base, tilt, translation | $20-$100 each | Component positioning |
| Fiber components | SM fiber, connectors | $50-$300 | Beam delivery/collection |

### 6. Electronics & Control

| Component | Specification | Cost Range | Purpose |
|-----------|---------------|------------|---------|
| Laser driver | TTL input, current control | $100-$400 | Laser power control |
| Detector controller | PIVOT bias, timing | $200-$600 | APD/SPAD operation |
| Computer | Linux, Python support | $500-$1500 | Data acquisition/analysis |
| Power supply | Lab-grade, isolated | $200-$800 | Equipment power |

## Budget Summary

| Level | Total Cost | Components Included |
|-------|------------|---------------------|
| **Minimal DIY** | $1,250 | Basic laser, 1 BBO crystal, 2 SPADs, breadboard, mounts |
| **Intermediate** | $3,500 | Better laser, multiple crystals, coincidence FPGA, full mount system |
| **Advanced** | $5,300+ | Tunable laser, multiple detectors, isolation table, complete electronics |

## Educational Resources

### Open-Source Projects

- **EduQ V1**: Complete DIY quantum optics setup with FPGA-based coincidence detection
  - GitHub: https://github.com/yallain/EduQ-V1
  - Features: SPDC source, entanglement experiments, QKD protocols
  - License: Hardware CERN OHL v2, Software AGPL/Apache 2.0

### Key Experiments

1. **Bell/CHSH Tests**: Evidence of non-local correlations
2. **Polarization Tomography**: Quantum state reconstruction
3. **g²(0) Measurement**: Confirm non-classical light (g² < 1)
4. **Quantum Key Distribution**: Practical QKD implementation

## Supplier Recommendations

### Reliable Sources

| Supplier | Specialty | Typical Lead Time |
|----------|-----------|-------------------|
| Thorlabs | Single photon detectors, precision optics | 2-4 weeks |
| Edmund Optics | Optical tables, mounts, filters | 1-3 weeks |
| qutools | Quantum optics kits | 4-8 weeks |
| M-Squared | Laser sources, crystals | 2-6 weeks |

## Setup Challenges

### Critical Pitfalls

1. **Vibration isolation**: Table must be completely stable for interferometry
2. **Beam alignment**: Requires careful iterative adjustment
3. **Polarization stability**: Temperature and vibration affect alignment
4. **Dark counts**: SPADs have background noise (typically 100-1000 counts/sec)
5. **Coupling efficiency**: Fiber coupling requires sub-micron precision

### Recommended Approach

1. Start with educational platform (EduQ V1)
2. Prioritize detector quality over other components
3. Use commercial suppliers for reliability
4. Document alignment procedures for reproducibility
5. Begin with simple experiments before complex ones

## Quick Component Checklist

- [ ] 405 nm laser diode (10-100 mW)
- [ ] BBO crystal (cut for 405 nm pumping)
- [ ] Polarizing beam splitter (PBS)
- [ ] Half-wave plate (HWP)
- [ ] Quarter-wave plate (QWP)
- [ ] Single photon detectors (2+ for coincidence)
- [ ] Optical breadboard (1m x 0.5m minimum)
- [ ] Kinematic mounts (10+ positions)
- [ ] Coincidence counter (FPGA-based preferred)
- [ ] Vibration isolation table (recommended for intermediate+)
- [ ] Computer with Python environment

## Notes

- All costs are estimates and vary by supplier/region
- SPDC coincidence rates: ~1-1.5 kHz for DIY setup
- g²(0) < 1 confirms quantum nature of light
- Bell test requires careful timing and polarization control
- Consider starting with simulations before physical implementation
