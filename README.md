# Ventoy partition size as a percentage

`ventoy-percent` calculates Ventoy's `-r` argument from the percentage of the
**whole disk** you want for the first partition, where your ISO files go.
For example, `--iso-percent 15` targets approximately 15% for that partition.
The remainder, minus Ventoy's boot data, stays unallocated for later partitions.

This is a Linux wrapper around your existing Ventoy installation. It does not
resize an existing installation or preserve existing partitions. **Executing an
installation erases the disk's existing data.** Select the whole disk, such as
`/dev/sda`, rather than a partition such as `/dev/sda1`.

## Requirements

Python 3.9 or newer, `lsblk` from util-linux, and an installed Ventoy CLI.
On this Arch Linux host all three were already present when checked.
If Python or lsblk is missing on Arch, install them first:

```sh
sudo pacman -S --needed python util-linux
```

Keep using your existing `ventoy` installation. For an extracted official Linux
release, supply `--ventoy /absolute/path/to/Ventoy2Disk.sh` instead. The script
must be executable and its accompanying Ventoy files must remain in place.

## Run in sequence

From the folder containing `ventoy-percent`, identify the intended disk:

```sh
lsblk -o NAME,SIZE,MODEL,TRAN,MOUNTPOINTS
```

Preview the calculation and generated command without writing anything:

```sh
./ventoy-percent --iso-percent 15 -g -L iso_disk /dev/sda
```

After verifying the selected disk and backing up its contents, install:

```sh
sudo ./ventoy-percent --iso-percent 15 -g -L iso_disk --execute /dev/sda
```

The wrapper uses Ventoy's `-i` install action and retains Ventoy's confirmation
prompts. Ventoy refuses `-i` when it detects an existing Ventoy installation.
This wrapper intentionally has no force-reinstall option. A changed percentage
does not resize an existing installation.

The wrapper rejects execution when lsblk shows mounted filesystems, active swap,
or active device mappings on the disk. Unmount/deactivate those first.

Fractional percentages work, with an optional percent sign:

```sh
./ventoy-percent --iso-percent 12.5% -g -L iso_disk /dev/sda
./ventoy-percent --help
```

For an optional system-wide command, install the wrapper and then use it without
the `./` prefix:

```sh
sudo install -m 0755 ventoy-percent /usr/local/bin/ventoy-percent
ventoy-percent --iso-percent 15 -g -L iso_disk /dev/sda
```

## Calculation and limits

Ventoy takes `-r` in **MiB** (1,048,576 bytes). Its layout uses a 1 MiB initial
area, a 32 MiB boot partition, a GPT trailer when applicable, and 4 KiB alignment.
The wrapper subtracts those before converting the desired ISO size into reserved
MiB. It rounds the reservation upward, so the estimated ISO partition is at most
the requested percentage and less than 1 MiB smaller than the target.
Filesystem metadata reduces the usable space inside that partition further.

For a 500,107,862,016-byte disk at 15% with GPT, the generated reservation is
`-r 405367`, and the first partition is approximately 69.86 GiB.

The calculation matches the installed Ventoy 1.1.18 shell partitioning code.
It supports disks with 512-byte logical sectors, including 512e drives, and
requires `-g` above 2 TiB. It rejects ISO targets below 32 MiB and percentages
that leave insufficient space for boot data. Labels allow 1-15 ASCII letters,
digits, underscores, dots and hyphens, starting with a letter, digit or
underscore; this avoids argument splitting in Ventoy's shell launcher.

The reserved space remains **unallocated**, and additional partitions must be
created separately. Leave Ventoy's first and second partitions in place.
Reserving space does not preserve partitions that existed before installation.

Sources, checked 2026-10-14:

- [Ventoy Linux CLI](https://www.ventoy.net/en/doc_start.html)
- [GPT layout and reserved space](https://www.ventoy.net/en/doc_disk_layout_gpt.html)
- [Ventoy partitioning source](https://github.com/ventoy/Ventoy/blob/master/INSTALL/tool/ventoy_lib.sh)

## Verification

Run the disk-free tests and lint check:

```sh
python -m unittest -v test_ventoy_percent.py
ruff check ventoy-percent test_ventoy_percent.py
```

The tests use simulated disk metadata and mocked execution. They do not install
Ventoy or change a partition table.
