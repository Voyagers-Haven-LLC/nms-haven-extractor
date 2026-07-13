================================================================================
           HAVEN EXTRACTOR - OFFSET DISCOVERY TOOLS
================================================================================

These tools help find the correct memory offsets for extracting data from
No Man's Sky. The offsets may shift between game versions.

================================================================================
QUICK START (Current NMS Version)
================================================================================

1. Double-click: RUN_SCANNER.bat
   - This installs the scanner mod to pyMHF

2. Open a NEW command prompt and run:
   pymhf NMS.exe

3. Load your save and enter any solar system

4. Watch the pyMHF console - it will show scan progress

5. After scanning completes, close NMS

6. Double-click: ANALYZE_DUMP.bat
   - This analyzes the memory dumps and shows offset info

================================================================================
BATCH FILES INCLUDED
================================================================================

RUN_SCANNER.bat     - Install scanner mod for current NMS version
ANALYZE_DUMP.bat    - Analyze memory dumps offline
RUN_DEBUG_TEST.bat  - Install test mod for Fractal413 debug version

================================================================================
PYTHON SCRIPTS
================================================================================

offset_scanner.py     - pyMHF mod that scans memory when you enter a system
                        Creates .bin dumps for offline analysis
                        Creates .json with scan results
                        Creates .hex for manual hex viewing

analyze_dump.py       - Offline analyzer for .bin dumps
                        Tests known 4.13 offsets
                        Scans for patterns if offsets are wrong

debug_offset_test.py  - Compares direct reads vs NMS.py struct access
                        For verifying offsets on Fractal413 debug build

verify_offsets.py     - Library for offset verification
                        Import in your own test scripts

================================================================================
OUTPUT LOCATIONS
================================================================================

Scanner output:  %USERPROFILE%\Documents\Haven-Extractor\offset_scans\
Debug output:    %USERPROFILE%\Documents\Haven-Extractor\debug_tests\

================================================================================
FRACTAL413 DEBUG VERSION
================================================================================

Fractal413 is the NMS 4.13 debug build with PDB symbols.
The offsets in our tools are based on this version.

To test on Fractal413:
1. Download and install Fractal413
2. Download RunAsDate from NirSoft
3. Configure RunAsDate to set date before September 28, 2023
4. Launch NMS through RunAsDate
5. Use RUN_DEBUG_TEST.bat to install the verification mod

================================================================================
INTERPRETING RESULTS
================================================================================

When you run ANALYZE_DUMP.bat, look for:

1. "Valid fields" count - if most fields are valid, offsets are correct
2. Planet data - biomes should be 0-15, sizes should be 0-4
3. Trading/Wealth/Conflict - values should be small integers (0-6)

If many fields show "Unknown" or garbage values:
- The offsets have shifted in your game version
- Compare the "pattern scan" results to find new offsets
- Look for clusters where Trading+Wealth appear together

================================================================================
NEED HELP?
================================================================================

1. Check the pyMHF console for error messages
2. Ensure pyMHF and NMS.py are properly installed
3. Make sure you're entering a solar system (not in space station menu)

================================================================================
