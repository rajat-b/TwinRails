"""Wrap a label's text to the width it is actually given."""

import tkinter as tk


def wrap_to_width(label: tk.Label) -> None:
    """Re-wrap `label` whenever its width changes, so its text fills its box.

    Replaces fixed pixel wraplengths, which ignored display scaling and left
    the right part of wide boxes empty (found 2026-09-25, once the text got
    bigger). The label MUST get its width from the layout -- packed with
    fill=X, or expand in a row. Sized by its own text instead, every re-wrap
    would narrow it and trigger another, shrinking it to nothing. Its
    starting wraplength stays as the width it asks for before the first
    layout, so it never pushes a window wider."""
    def on_configure(event):
        inset = 2 * (label.winfo_pixels(label.cget("padx"))
                     + label.winfo_pixels(label.cget("borderwidth"))
                     + label.winfo_pixels(label.cget("highlightthickness")))
        width = event.width - inset
        if width <= 1:
            return      # not laid out yet
        if label.winfo_pixels(label.cget("wraplength")) != width:
            label.configure(wraplength=width)
    label.bind("<Configure>", on_configure, add="+")
