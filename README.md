SDR RTL FM RDS Scanner (English Version)

A powerful tool for automatic scanning of the FM band (87.5–108.0 MHz) and collecting RDS (Radio Data System) data using an RTL-SDR receiver. Focused on Saint Petersburg (SPB) database creation, but easily adaptable to any region.

Key feature: The project uses the rtl_fm (signal capture) + RedSea (RDS decoding) pipeline, ensuring high accuracy in PI code and station text recognition even with weak signals.
🎯 What the Scanner Does

    Automatic band scanning with configurable step and dwell time.
    RDS decoding via RedSea: reliable extraction of PI codes, PS (Program Service), and RT (Radio Text).
    Filtering and structuring: saving only valid stations with metadata.
    Flexible region configuration: support for different geographic zones via JSON configs.
    Temporary file ignoring: .gitignore configured to keep only the database in the repo.

📦 Requirements
Hardware

    RTL-SDR receiver (e.g., RTL2832U).

Software (OS: Linux)

    Python 3.x and required libraries.
    RedSea — command-line RDS decoder (mandatory!).
    rtl-sdr utilities (specifically rtl_fm).

Installation Steps

    Python dependencies:

    bash

    pip install pyrtlsdr numpy pandas

    RedSea and rtl-sdr utilities:

    Option A (via package manager — recommended for Ubuntu/Debian):

    bash

    sudo apt update
    sudo apt install librtlsdr-dev rtl-sdr
    sudo snap install redsea

    Option B (build RedSea from source — if snap is unavailable):

    bash

    git clone https://github.com/Osso/redsea.git
    cd redsea
    mkdir build && cd build
    cmake ..
    make
    sudo make install

        ⚠️ Important: Verify that rtl_fm and redsea commands are available:

        bash

        rtl_fm -h
        redsea -h

        If you get «command not found», the binaries are not in your \$PATH.

🚀 Quick Start

    Ensure RedSea and rtl_fm are installed.
    Clone the repository (or ensure you’re in the project folder).
    Check the region config in regions/ (use spb.json for Saint Petersburg).
    Run the scanner:

    bash

    python3 SDR_RTL_FM_RDS_Scaner.py

    Wait for the scan to finish. Results saved to data/SDR_FM_RDS_Base_spb.json.

    ⚠️ Important: The scanner needs USB device access rights. Use sudo or set up udev rules for RTL-SDR if you get permission errors.

🗺️ Customizing for Your Region

To scan a different city or frequency range:

    Create a new JSON file in regions/ (e.g., msk.json).
    Specify parameters (see example in the Russian section).
    Update the script (SDR_RTL_FM_RDS_Scaner.py) to use this file or pass it as an argument.

📁 Project Structure

    data/ — stores station databases and temporary files (ignored by Git).
    regions/ — configurations for different regions.
    SDR_RTL_FM_RDS_Scaner.py — main scanner script.
    .gitignore — configured to exclude temporary scan logs.
    LICENSE — MIT license.

🤝 How to Contribute

The project is open to community contributions! You can:

    Add configurations for new regions.
    Optimize the RDS packet detection algorithm.
    Write installation docs for Windows/macOS.
    Suggest new features (e.g., coverage visualization or CSV export).

Send a Pull Request or open an Issue — any help is welcome!
📜 License

The project is distributed under the MIT license. See the LICENSE file for details.
