#!/usr/bin/env python3
"""
MIDIglue (1.1)
======================

Combines up to 16 separate Type 0 MIDI files into a single Type 1 MIDI file,
one file per MIDI channel (1-16). Channel 10 is treated as the rhythm/drum
channel. The tempo of the exported file is entered manually in BPM in the GUI.

Requirements
------------
Python 3.7+ with Tkinter (Tkinter ships with the standard python.org
installers for both Windows and macOS). No other dependencies -- the MIDI
reading/writing/merging logic below is implemented from scratch using only
the Python standard library, so there is nothing to `pip install`.


Notes / limitations
--------------------
- Input files should use ticks-per-quarter-note timing (this covers the vast
  majority of MIDI files; SMPTE/frame-based timing is not supported).
- If a file assigned to a channel isn't Type 0, its track(s) are merged and
  used anyway.
- SysEx events are dropped for simplicity; standard note/controller/program
  data is preserved.
"""

import os
import sys

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except ImportError:
    sys.stderr.write(
        "This program needs Tkinter, which normally ships with Python.\n"
        " - Windows: reinstall Python from python.org (Tkinter is included by default).\n"
        " - macOS: install Python from python.org. If you used Homebrew, try:\n"
        "     brew install python-tk\n"
        " - Linux: sudo apt install python3-tk  (or your distro's equivalent)\n"
    )
    sys.exit(1)

GM_INSTRUMENTS = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano",
    "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavinet",
    "Celesta", "Glockenspiel", "Music Box", "Vibraphone",
    "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
    "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)", "Electric Guitar (jazz)", "Electric Guitar (clean)",
    "Electric Guitar (muted)", "Overdriven Guitar", "Distortion Guitar", "Guitar Harmonics",
    "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass",
    "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2",
    "Violin", "Viola", "Cello", "Contrabass",
    "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
    "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2",
    "Choir Aahs", "Voice Oohs", "Synth Voice", "Orchestra Hit",
    "Trumpet", "Trombone", "Tuba", "Muted Trumpet",
    "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2",
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax",
    "Oboe", "English Horn", "Bassoon", "Clarinet",
    "Piccolo", "Flute", "Recorder", "Pan Flute",
    "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)", "Lead 4 (chiff)",
    "Lead 5 (charang)", "Lead 6 (voice)", "Lead 7 (fifths)", "Lead 8 (bass + lead)",
    "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)",
    "Pad 5 (bowed)", "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)",
    "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)",
    "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)",
    "Sitar", "Banjo", "Shamisen", "Koto",
    "Kalimba", "Bag Pipe", "Fiddle", "Shanai",
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock",
    "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet",
    "Telephone Ring", "Helicopter", "Applause", "Gunshot",
]
assert len(GM_INSTRUMENTS) == 128

GM_INSTRUMENTS = [f"{i + 1}  {name}" for i, name in enumerate(GM_INSTRUMENTS)]

DRUM_KITS = [
    ("0  Standard Kit", 0),
    ("8  Room Kit", 8),
    ("16  Power Kit", 16),
    ("24  Electronic Kit", 24),
    ("25  TR-808 Kit", 25),
    ("32  Jazz Kit", 32),
    ("40  Brush Kit", 40),
    ("48  Orchestra Kit", 48),
    ("56  SFX Kit", 56),
]

class MidiFormatError(Exception):
    """Raised when a file can't be parsed as a standard MIDI file."""


def read_varlen(data, pos):
    value = 0
    while True:
        b = data[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not (b & 0x80):
            break
    return value, pos


def write_varlen(value):
    if value < 0:
        raise ValueError("negative delta time")
    chunks = [value & 0x7F]
    value >>= 7
    while value:
        chunks.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(chunks))


def parse_track(data):
    events = []
    pos = 0
    n = len(data)
    running_status = None
    while pos < n:
        delta, pos = read_varlen(data, pos)
        peek = data[pos]
        if peek & 0x80:
            status = peek
            pos += 1
            if status < 0xF0:
                running_status = status
        else:
            if running_status is None:
                raise MidiFormatError(f"Corrupt MIDI data: expected a status byte at position {pos}")
            status = running_status

        if status == 0xFF:
            meta_type = data[pos]
            pos += 1
            length, pos = read_varlen(data, pos)
            payload = bytes(data[pos:pos + length])
            pos += length
            events.append([delta, 'meta', meta_type, None, payload])
        elif status in (0xF0, 0xF7):
            length, pos = read_varlen(data, pos)
            payload = bytes(data[pos:pos + length])
            pos += length
            events.append([delta, 'sysex', status, None, payload])
        else:
            hi = status & 0xF0
            ch = status & 0x0F
            nbytes = 1 if hi in (0xC0, 0xD0) else 2
            payload = bytes(data[pos:pos + nbytes])
            pos += nbytes
            events.append([delta, 'midi', hi, ch, payload])
    return events


def write_track_data(events):
    out = bytearray()
    for delta, kind, subtype, channel, payload in events:
        out += write_varlen(delta)
        if kind == 'midi':
            out.append(subtype | (channel & 0x0F))
            out += payload
        elif kind == 'meta':
            out.append(0xFF)
            out.append(subtype)
            out += write_varlen(len(payload))
            out += payload
        elif kind == 'sysex':
            out.append(subtype)
            out += write_varlen(len(payload))
            out += payload
        else:
            raise ValueError(f"unknown event kind {kind!r}")
    return bytes(out)


def read_midi_file(path):
    with open(path, 'rb') as f:
        data = f.read()
    if len(data) < 14 or data[0:4] != b'MThd':
        raise MidiFormatError("Not a valid MIDI file (missing MThd header).")
    header_len = int.from_bytes(data[4:8], 'big')
    fmt = int.from_bytes(data[8:10], 'big')
    ntracks = int.from_bytes(data[10:12], 'big')
    division = int.from_bytes(data[12:14], 'big')
    if division & 0x8000:
        raise MidiFormatError(
            "This file uses SMPTE frame-based timing, which isn't supported. "
            "Please use a file with ticks-per-quarter-note timing."
        )
    pos = 8 + header_len
    tracks = []
    for _ in range(ntracks):
        if data[pos:pos + 4] != b'MTrk':
            raise MidiFormatError("Malformed MIDI file: expected an MTrk chunk.")
        track_len = int.from_bytes(data[pos + 4:pos + 8], 'big')
        track_bytes = data[pos + 8:pos + 8 + track_len]
        tracks.append(parse_track(track_bytes))
        pos += 8 + track_len
    return fmt, division, tracks


def write_midi_file(path, division, tracks):
    fmt = 1 if len(tracks) > 1 else 0
    with open(path, 'wb') as f:
        f.write(b'MThd')
        f.write((6).to_bytes(4, 'big'))
        f.write(fmt.to_bytes(2, 'big'))
        f.write(len(tracks).to_bytes(2, 'big'))
        f.write(division.to_bytes(2, 'big'))
        for events in tracks:
            track_bytes = write_track_data(events)
            f.write(b'MTrk')
            f.write(len(track_bytes).to_bytes(4, 'big'))
            f.write(track_bytes)


def merge_track_events(tracks):
    """Flatten possibly-multiple parallel tracks into one delta-time event stream."""
    if len(tracks) == 1:
        return [list(e) for e in tracks[0]]
    absolute_events = []
    for track in tracks:
        t = 0
        for delta, kind, subtype, channel, payload in track:
            t += delta
            absolute_events.append([t, kind, subtype, channel, payload])
    absolute_events.sort(key=lambda e: e[0])
    merged = []
    prev_t = 0
    for t, kind, subtype, channel, payload in absolute_events:
        merged.append([t - prev_t, kind, subtype, channel, payload])
        prev_t = t
    return merged


META_SET_TEMPO = 0x51
META_TIME_SIGNATURE = 0x58
META_END_OF_TRACK = 0x2F
META_TRACK_NAME = 0x03

DROP_META_FROM_CHANNEL_TRACK = {META_SET_TEMPO, META_TIME_SIGNATURE, META_END_OF_TRACK, META_TRACK_NAME}


def get_tempo_and_time_sig(events):
    tempo = None
    time_sig = None
    for delta, kind, subtype, channel, payload in events:
        if kind == 'meta' and subtype == META_SET_TEMPO and tempo is None:
            tempo = int.from_bytes(payload, 'big')
        elif kind == 'meta' and subtype == META_TIME_SIGNATURE and time_sig is None:
            time_sig = payload
    return tempo, time_sig


def build_tempo_track(bpm, time_sig_payload):
    if bpm <= 0:
        raise ValueError("Tempo must be greater than 0 BPM.")
    tempo_us_per_quarter = round(60_000_000 / bpm)
    if tempo_us_per_quarter > 0xFFFFFF:
        raise ValueError("Tempo is too slow for the MIDI tempo format.")
    events = [
        [0, 'meta', META_TRACK_NAME, None, b'Tempo Track'],
        [0, 'meta', META_SET_TEMPO, None, tempo_us_per_quarter.to_bytes(3, 'big')],
    ]
    if time_sig_payload:
        events.append([0, 'meta', META_TIME_SIGNATURE, None, time_sig_payload])
    events.append([0, 'meta', META_END_OF_TRACK, None, b''])
    return events


def transpose_note_events(payload, semitones):
    if not semitones:
        return payload
    note = max(0, min(127, payload[0] + semitones))
    return bytes([note]) + payload[1:]


def build_channel_track(events, target_channel, program, scale, label, transpose=0):
    new_events = [
        [0, 'meta', META_TRACK_NAME, None, label.encode('ascii', 'replace')],
        [0, 'midi', 0xC0, target_channel, bytes([program & 0x7F])],
    ]
    carry = 0
    for delta, kind, subtype, channel, payload in events:
        scaled = int(round(delta * scale))
        keep = True
        if kind == 'meta' and subtype in DROP_META_FROM_CHANNEL_TRACK:
            keep = False
        elif kind == 'midi' and subtype == 0xC0:  
            keep = False
        elif kind == 'sysex':  
            keep = False
        if not keep:
            carry += scaled
            continue
        total = carry + scaled
        carry = 0
        if kind == 'midi':
            if subtype in (0x80, 0x90) and len(payload) >= 2:
                payload = transpose_note_events(payload, transpose)
            new_events.append([total, 'midi', subtype, target_channel, payload])
        else:  
            new_events.append([total, 'meta', subtype, None, payload])
    new_events.append([carry, 'meta', META_END_OF_TRACK, None, b''])
    return new_events


class CombineError(Exception):
    """Raised for user-facing problems combining files (e.g. nothing assigned)."""


def combine_midi_files(channel_assignments, output_path, bpm, transpose=0):
    """
    channel_assignments: list of length 16. Each entry is either None, or a
        dict {'path': <file path str>, 'program': <0-127 int>}.
    output_path: where to write the resulting Type 1 .mid file.
    bpm: manually entered tempo in beats per minute.
    transpose: semitone transposition applied to melodic note events.
               Channel 10 (drums) is left untransposed.
    Returns a list of warning strings (e.g. about non-Type-0 input files).
    """
    warnings = []
    loaded = [(i, a) for i, a in enumerate(channel_assignments) if a is not None]
    if not loaded:
        raise CombineError("No channels have a MIDI file assigned.")

    ref_idx = 0 if channel_assignments[0] is not None else loaded[0][0]
    ref_path = channel_assignments[ref_idx]['path']
    ref_fmt, ref_division, ref_tracks = read_midi_file(ref_path)
    ref_events = merge_track_events(ref_tracks)
    _, time_sig = get_tempo_and_time_sig(ref_events)

    if not (1 <= bpm <= 999):
        raise CombineError("Tempo must be between 1 and 999 BPM.")

    out_tracks = [build_tempo_track(bpm, time_sig)]

    for idx, assignment in loaded:
        path = assignment['path']
        program = assignment['program']
        fmt, division, tracks = read_midi_file(path)
        if fmt not in (0, 1):
            warnings.append(f"Channel {idx + 1} file is MIDI Type {fmt}; tracks may not merge as expected.")
        elif fmt == 1 and len(tracks) > 1:
            warnings.append(f"Channel {idx + 1} file has multiple tracks (Type 1); they were merged.")
        events = merge_track_events(tracks)
        scale = (ref_division / division) if division else 1.0
        label = f"Channel {idx + 1}"
        channel_transpose = 0 if idx == 9 else transpose
        track = build_channel_track(events, idx, program, scale, label, channel_transpose)
        out_tracks.append(track)

    write_midi_file(output_path, ref_division, out_tracks)
    return warnings


# =============================================================================
# GUI
# =============================================================================

class App:
    def __init__(self, root):
        self.root = root
        root.title("MIDIglue")
        root.resizable(False, False)

        self.channel_paths = [None] * 16
        self.file_buttons = []
        self.instrument_combos = []
        self.status_var = tk.StringVar(value="Ready.")
        self.tempo_var = tk.StringVar(value="120")
        self.transpose_var = tk.StringVar(value="0")

        main = ttk.Frame(root, padding=12)
        main.grid(row=0, column=0, sticky="nsew")

        title_lbl = ttk.Label(main, text="MIDIglue (SMF 0 -> SMF 1)", font=("Segoe UI", 14, "bold"))
        title_lbl.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 2))

        subtitle = ttk.Label(
            main,
            text=("Load a Type 0 MIDI file into each channel you want to use and select a "
                  "patch for it.\n"
                  "Channel 10 (Rhythm track) is not transposed.\n"
		"Export will be SMF 1 multitrack MIDI"),
            foreground="#555555",
            wraplength=560,
            justify="left",
        )
        subtitle.grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 10))

        settings_frame = ttk.Frame(main)
        settings_frame.grid(row=2, column=0, columnspan=4, sticky="we", pady=(0, 8))

        ttk.Label(settings_frame, text="Tempo (BPM):").grid(row=0, column=0, sticky="w")
        tempo_spin = ttk.Spinbox(
            settings_frame,
            from_=1,
            to=999,
            textvariable=self.tempo_var,
            width=8,
            justify="right",
        )
        tempo_spin.grid(row=0, column=1, sticky="w", padx=(4, 20))

        ttk.Label(settings_frame, text="Transpose (semitones):").grid(row=0, column=2, sticky="w")
        transpose_spin = ttk.Spinbox(
            settings_frame,
            from_=-127,
            to=127,
            textvariable=self.transpose_var,
            width=8,
            justify="right",
        )
        transpose_spin.grid(row=0, column=3, sticky="w", padx=(4, 0))

        for i in range(16):
            r = i + 3
            label_text = f"Ch {i + 1} (Rhythm)" if i == 9 else f"Ch {i + 1}"
            lbl = ttk.Label(main, text=label_text, width=15)
            lbl.grid(row=r, column=0, sticky="w", pady=2)

            btn = tk.Button(
                main, text="Click to choose file\u2026", anchor="w",
                command=lambda ch=i: self.choose_file(ch), width=32,
            )
            btn.grid(row=r, column=1, sticky="we", padx=(4, 8), pady=2)
            self.file_buttons.append(btn)

            values = [name for name, _ in DRUM_KITS] if i == 9 else GM_INSTRUMENTS
            combo = ttk.Combobox(main, values=values, state="readonly", width=26)
            combo.current(0)
            combo.grid(row=r, column=2, sticky="we", pady=2)
            self.instrument_combos.append(combo)

            clr = tk.Button(
                main, text="\u2715", width=2, fg="#999999", relief="flat",
                command=lambda ch=i: self.clear_channel(ch),
            )
            clr.grid(row=r, column=3, sticky="w", padx=(6, 0), pady=2)

        export_btn = ttk.Button(main, text="Export Combined MIDI...", command=self.do_export)
        export_btn.grid(row=19, column=0, columnspan=4, sticky="we", pady=(14, 6))


        main.columnconfigure(1, weight=1)


    # -- helpers ---------------------------------------------------------

    def _update_file_button(self, ch, filename):
        display = filename if len(filename) <= 30 else filename[:27] + "..."
        self.file_buttons[ch].config(text=display)

    def get_program_for_channel(self, ch):
        combo = self.instrument_combos[ch]
        idx = combo.current()
        if idx < 0:
            idx = 0
        if ch == 9:
            return DRUM_KITS[idx][1]
        return idx

    # -- callbacks ---------------------------------------------------------

    def choose_file(self, ch):
        path = filedialog.askopenfilename(
            title=f"Select a Type 0 MIDI file for Channel {ch + 1}",
            filetypes=[("MIDI files", "*.mid *.midi"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            fmt, division, tracks = read_midi_file(path)
        except MidiFormatError as e:
            messagebox.showerror("Invalid MIDI file", str(e))
            return
        except Exception as e:
            messagebox.showerror("Error reading file", f"Could not read this file:\n{e}")
            return

        if fmt not in (0, 1):
            proceed = messagebox.askyesno(
                "Unexpected MIDI type",
                f"This file is MIDI Type {fmt}, not the expected Type 0.\n"
                f"It will still be imported, but the result may not sound as expected.\n\n"
                f"Continue anyway?",
            )
            if not proceed:
                return

        self.channel_paths[ch] = path
        filename = os.path.basename(path)
        self._update_file_button(ch, filename)
        self.status_var.set(f"Channel {ch + 1}: loaded \u201c{filename}\u201d")

    def clear_channel(self, ch):
        self.channel_paths[ch] = None
        self.file_buttons[ch].config(text="Click to choose file\u2026")
        self.status_var.set(f"Channel {ch + 1}: cleared")

    def do_export(self):
        assignments = []
        for ch in range(16):
            path = self.channel_paths[ch]
            if path is None:
                assignments.append(None)
            else:
                assignments.append({'path': path, 'program': self.get_program_for_channel(ch)})

        if all(a is None for a in assignments):
            messagebox.showwarning("Nothing to export", "Please load at least one MIDI file into a channel first.")
            return

        try:
            bpm = float(self.tempo_var.get())
            transpose = int(self.transpose_var.get())
        except ValueError:
            messagebox.showerror(
                "Invalid settings",
                "Tempo must be a number and transposition must be a whole number of semitones.",
            )
            return

        if not (1 <= bpm <= 999):
            messagebox.showerror("Invalid tempo", "Tempo must be between 1 and 999 BPM.")
            return

        if not (-127 <= transpose <= 127):
            messagebox.showerror("Invalid transposition", "Transpose must be between -127 and +127 semitones.")
            return

        save_path = filedialog.asksaveasfilename(
            title="Save Combined MIDI File",
            defaultextension=".mid",
            filetypes=[("MIDI File", "*.mid"), ("All files", "*.*")],
        )
        if not save_path:
            return

        self.status_var.set("Exporting\u2026")
        self.root.update_idletasks()

        try:
            warnings = combine_midi_files(assignments, save_path, bpm, transpose)
        except (CombineError, MidiFormatError) as e:
            messagebox.showerror("Cannot export", str(e))
            self.status_var.set("Export failed.")
            return
        except Exception as e:
            messagebox.showerror("Export failed", f"An unexpected error occurred:\n{e}")
            self.status_var.set("Export failed.")
            return

        self.status_var.set(f"Exported to {os.path.basename(save_path)}")
        if warnings:
            messagebox.showinfo(
                "Exported (with notes)",
                "File exported successfully.\n\nNotes:\n- " + "\n- ".join(warnings),
            )
        else:
            messagebox.showinfo("Exported", f"Combined MIDI file saved to:\n{save_path}")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
