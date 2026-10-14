# Calculate Ventoy's reserved space with Bash, lsblk and Python

This example calculates the space Ventoy should reserve when approximately
**15% of the whole disk** should become the first partition for ISO files.
The remaining space, after allowing for Ventoy's boot data, stays unallocated
for partitions you can create later.

The calculation uses `lsblk` to read disk capacity, Python to calculate the
reservation, and Bash to store the result in `reserve_mib`.

## Requirements and assumptions

- Bash, Python 3.9 or newer, and `lsblk` from util-linux.
- An existing Ventoy CLI installation for the printed installation command.
- GPT partitioning, selected by Ventoy's `-g` option.
- A disk with 512-byte logical sectors, including a 512e disk.
- A whole-disk device such as `/dev/sda`, rather than `/dev/sda1`.

These commands calculate a value and **print** an installation command. They do
not install Ventoy or write to the disk.

## 1. Enter Bash and choose the disk and percentage

If your current shell is Zsh, enter Bash first:

```sh
bash
```

Set the disk and the percentage for the ISO partition:

```bash
device=/dev/sda
iso_percent=15
```

You can also use a fractional percentage, such as `12.5` or `12.5%`.

## 2. Calculate and store the reserved space

Paste this block into the same Bash session:

```bash
if reserve_mib=$(
  python3 - "$(lsblk -bndo SIZE "$device")" "$iso_percent" <<'PY'
import sys
from fractions import Fraction

total_bytes = int(sys.argv[1])
percent = Fraction(sys.argv[2].removesuffix("%"))
mib = 1048576

if not 0 < percent < 100:
  raise SystemExit("Percentage must be above 0 and below 100.")

available = (
  (total_bytes - 32 * mib - 33 * 512) // 4096 * 4096 - mib
)
target = total_bytes * percent.numerator // (100 * percent.denominator)

if not 32 * mib <= target < available:
  raise SystemExit("Requested size leaves insufficient ISO or boot space.")

reserved = (available - target + mib - 1) // mib
print(reserved)
PY
); then
  printf 'Reserved space: %s MiB\n' "$reserve_mib"
  printf 'sudo ventoy -i -r %s -g -L iso_disk %q\n' \
    "$reserve_mib" "$device"
else
  printf 'Calculation failed; no installation command generated.\n' >&2
fi
```

The `if` ensures that an installation command is printed only when the
calculation succeeds.

## How the nested commands work

1. `lsblk -bndo SIZE "$device"` reads the selected disk's capacity in bytes.
   Its `-b`, `-n`, `-d` and `-o SIZE` options select bytes, omit headings,
   omit dependent devices, and output only the size.
2. The inner `$(...)` passes that capacity to Python as its first argument.
   The percentage becomes its second argument.
3. `python3 -` reads the Python program from the following heredoc. Quoting
   `'PY'` prevents Bash from expanding anything inside the Python code.
4. Python allows for the 32 MiB boot partition, GPT trailer, 4 KiB alignment,
   and 1 MiB initial area. It rounds the reservation upward to whole MiB.
5. Python prints one integer. The outer `$(...)` captures it and assigns it
   to the Bash variable `reserve_mib`.

After a successful calculation, the result remains available in Bash:

```bash
printf '%s\n' "$reserve_mib"
```

The ISO partition is approximately the requested percentage; filesystem
metadata reduces the usable space inside it further.

## Before running the printed command

**Running the printed Ventoy installation command erases existing disk data.**
Verify the selected disk and back up its contents first. The command uses
Ventoy's `-i` installation action; it does not resize an existing installation.
Ventoy refuses `-i` when it detects an existing Ventoy installation.

Reserved space remains unallocated. Create additional partitions there after
installation, keeping Ventoy's first and second partitions in place.

See [Ventoy's Linux CLI documentation](https://www.ventoy.net/en/doc_start.html)
and [its GPT layout documentation](https://www.ventoy.net/en/doc_disk_layout_gpt.html).
