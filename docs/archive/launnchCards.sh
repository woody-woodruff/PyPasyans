#!/bin/bash
# Launch the Pasyans engine (v4) at 200px cards with a 15px match border.
# Fixed: previously pointed at RussianTarot.py, which does not exist.
cd "$(dirname "$0")"
python3 pasyans-4.py -s=200 -b=15 &
