# GWS Raw File Export

`scripts/gws_export_files.py` exports raw GWS JSON rows into client-ready zip archives.

The archive unit is:

```text
track code + calendar year + file type
```

Example:

```text
SAR-2025-Cycle.zip
SAR-2025-FinalCycle.zip
```

`--track-code` accepts either one code or a comma-separated list. A multi-track run still keeps archives separate:

```text
SAR-2025-Cycle.zip
BEL-2025-Cycle.zip
AQU-2025-Cycle.zip
```

Inside each archive:

```text
2025-07-01/original-file-name.json
2025-07-02/original-file-name.json
manifest.json
metadata.json
```

The script streams rows from SQL Server directly into the zip file. It does not create a loose raw-file directory.
