# Fun Demo Scripts

This folder contains fun demo scripts and experiments for the Waveshare 2.4" LCD display.

## Scripts

### `hello_world.py`
Basic "Hello World" test for the LCD display. Great for verifying your hardware setup works.

```bash
python fun/hello_world.py
```

### `rickroll.py`
Displays the classic "Never Gonna Give You Up" lyrics on the LCD. A fun way to test text scrolling and display updates.

```bash
python fun/rickroll.py
```

### `barcode_rickroll.py`
Scans a barcode and rickrolls you on the LCD. Combines barcode scanning with the rickroll lyrics.

```bash
python fun/barcode_rickroll.py
```

### `display_fact.py`
Displays random fun facts on the LCD. Fetches facts from an API and shows them on screen.

```bash
python fun/display_fact.py
```

### `fetch_info.py`
Simple utility to fetch information from APIs. Used for testing HTTP requests and data parsing.

```bash
python fun/fetch_info.py
```

### `dynamic_updater.py`
Demonstrates dynamic display updates with real-time information. Shows how to continuously update the LCD with changing data.

```bash
python fun/dynamic_updater.py
```

## Purpose

These scripts are kept for:
- Learning how the LCD display works
- Testing hardware functionality
- Having fun with the display
- Reference examples for new developers

For production use, see `barcode_inventree.py` in the main directory.
