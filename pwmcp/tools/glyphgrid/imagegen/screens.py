"""Text screens: a cols x rows grid of cells (char, fg, bg, flags) plus an optional cursor.

Flags: R reverse video, B box-drawing/block character, C cursor cell, A ambiguous (the font draws another
repertoire character with the identical bitmap, so no reader can know which).
"""
import random

PAL = {
    "black": (0, 0, 0), "blue": (0, 0, 170), "green": (0, 170, 0), "cyan": (0, 170, 170),
    "red": (170, 0, 0), "magenta": (170, 0, 170), "brown": (170, 85, 0), "grey": (170, 170, 170),
    "dgrey": (85, 85, 85), "lblue": (85, 85, 255), "lgreen": (85, 255, 85), "lcyan": (85, 255, 255),
    "lred": (255, 85, 85), "lmagenta": (255, 85, 255), "yellow": (255, 255, 85), "white": (255, 255, 255),
}
FALLBACK = {"┌": "+", "┐": "+", "└": "+", "┘": "+", "─": "-", "│": "|", "╔": "+", "╗": "+", "╚": "+", "╝": "+",
            "═": "=", "║": "|", "█": "#", "░": ".", "▒": ":", "▓": "#", "▲": "^", "▼": "v"}
SCHEMES = {"grey-on-black": ("grey", "black"), "white-on-blue": ("white", "blue"), "black-on-grey": ("black", "grey"),
           "green-on-black": ("lgreen", "black"), "yellow-on-blue": ("yellow", "blue")}
READABLE = [("grey", "black"), ("white", "blue"), ("black", "grey"), ("lgreen", "black"), ("yellow", "blue"),
            ("black", "cyan"), ("white", "red"), ("lcyan", "black"), ("black", "brown"), ("white", "dgrey")]


def is_box(ch):
    return 0x2500 <= ord(ch) <= 0x259F


class Cell:
    __slots__ = ("ch", "fg", "bg", "flags")

    def __init__(self, ch=" ", fg=PAL["grey"], bg=PAL["black"], flags=""):
        self.ch, self.fg, self.bg, self.flags = ch, fg, bg, flags


class Screen:
    def __init__(self, name, cols, rows, fg=PAL["grey"], bg=PAL["black"]):
        self.name, self.cols, self.rows = name, cols, rows
        self.cells = [[Cell(" ", fg, bg) for _ in range(cols)] for _ in range(rows)]
        self.cursor = None

    def put(self, r, c, text, fg=None, bg=None, reverse=False):
        for i, ch in enumerate(text):
            if 0 <= r < self.rows and 0 <= c + i < self.cols:
                cell = self.cells[r][c + i]
                f, b = (fg or cell.fg), (bg or cell.bg)
                if reverse:
                    f, b = b, f
                cell.ch, cell.fg, cell.bg = ch, f, b
                cell.flags = ("R" if reverse else "") + ("B" if is_box(ch) else "")

    def fill(self, r0, c0, r1, c1, bg, fg=None):
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                cell = self.cells[r][c]
                cell.ch, cell.bg, cell.flags = " ", bg, ""
                if fg:
                    cell.fg = fg

    def reverse(self, r, c0, c1):
        for c in range(c0, c1 + 1):
            cell = self.cells[r][c]
            cell.fg, cell.bg = cell.bg, cell.fg
            cell.flags = "R" + cell.flags.replace("R", "")

    def box(self, font, r0, c0, r1, c1, fg, bg, double=False):
        t = "╔═╗║╚╝" if double else "┌─┐│└┘"
        tl, h, tr, v, bl, br = (font.pick(x) for x in t)
        self.put(r0, c0, tl + h * (c1 - c0 - 1) + tr, fg, bg)
        self.put(r1, c0, bl + h * (c1 - c0 - 1) + br, fg, bg)
        for r in range(r0 + 1, r1):
            self.put(r, c0, v, fg, bg)
            self.put(r, c1, v, fg, bg)

    def text_rows(self):
        return ["".join(c.ch for c in row) for row in self.cells]

    def mark_ambiguous(self, font):
        for row in self.cells:
            for cell in row:
                if cell.ch != " " and font.ambiguous_with(cell.ch):
                    cell.flags += "A"


def alphabet(font, scheme="grey-on-black"):
    """Code chart: one row per 16 code points, a hex column header and row label; every repertoire character appears once."""
    fg, bg = (PAL[x] for x in SCHEMES[scheme])
    cps = sorted({ord(c) for c in font.repertoire()})
    rows = sorted({cp // 16 for cp in cps})
    s = Screen("alphabet", 5 + 32, 2 + len(rows), fg, bg)
    s.put(0, 0, ("ALPHABET " + font.id)[:s.cols])
    s.put(1, 0, "     " + " ".join("0123456789ABCDEF"))
    have = set(cps)
    for i, rw in enumerate(rows):
        s.put(2 + i, 0, "%04X:" % (rw * 16))
        for col in range(16):
            cp = rw * 16 + col
            if cp in have:
                s.put(2 + i, 5 + col * 2, chr(cp))
    s.mark_ambiguous(font)
    return s


def installer(font, cols=80, rows=25):
    """Installer-like screen: title bar, double-box menu with a reverse-video selection, progress bar, prompt with cursor."""
    s = Screen("installer", cols, rows, PAL["white"], PAL["blue"])
    s.fill(0, 0, 0, cols - 1, PAL["grey"], PAL["black"])
    s.put(0, 2, "Example OS 1.0 Installer", PAL["black"], PAL["grey"])
    s.put(0, cols - 14, "[F1] Help", PAL["black"], PAL["grey"])
    s.put(2, 2, "Select the installation target:", PAL["yellow"], PAL["blue"])
    top, left, w = 4, 6, 44
    s.box(font, top, left, top + 8, left + w, PAL["white"], PAL["blue"], double=True)
    items = ["1  Install to disk vda (20 GiB)", "2  Install to disk vdb (64 GiB)", "3  Rescue shell", "4  Memory test", "5  Reboot", "6  Power off"]
    for i, it in enumerate(items):
        s.put(top + 1 + i, left + 2, it.ljust(w - 3), PAL["white"], PAL["blue"])
    s.reverse(top + 2, left + 1, left + w - 1)
    s.put(top + 7, left + 2, "Use up/down, Enter to confirm", PAL["lcyan"], PAL["blue"])
    s.put(15, 6, "Status: ", PAL["grey"], PAL["blue"])
    s.put(15, 14, "OK", PAL["lgreen"], PAL["blue"])
    s.put(15, 18, "WARN", PAL["yellow"], PAL["blue"])
    s.put(15, 24, "FAIL", PAL["lred"], PAL["blue"])
    s.put(17, 6, "Copying files", PAL["white"], PAL["blue"])
    filled = 31
    s.put(18, 6, "[" + font.pick("█") * filled + font.pick("░") * (40 - filled) + "]", PAL["lgreen"], PAL["blue"])
    s.put(18, 49, "77%", PAL["white"], PAL["blue"])
    s.put(20, 6, "black on grey:", PAL["grey"], PAL["blue"])
    s.put(20, 21, " NOTE ", PAL["black"], PAL["grey"])
    s.put(rows - 2, 2, "root@install:~# ", PAL["lgreen"], PAL["blue"])
    s.put(rows - 2, 18, "ls /dev/vd*", PAL["white"], PAL["blue"])
    s.cursor = (rows - 2, 29)
    s.mark_ambiguous(font)
    return s


def random_text(font, seed, cols=80, rows=25):
    """Random-word stress screen: printable ASCII words in random readable colour pairs, a few reverse spans."""
    rng = random.Random("rand:%s:%s" % (font.id, seed))
    pool = [c for c in font.repertoire() if 0x21 <= ord(c) <= 0x7E]
    s = Screen("random-%s" % seed, cols, rows)
    for r in range(rows):
        c = rng.randrange(0, 3)
        while c < cols - 1:
            n = min(rng.randint(1, 12), cols - c)
            fg, bg = (PAL[x] for x in rng.choice(READABLE))
            word = "".join(rng.choice(pool) for _ in range(n))
            s.put(r, c, word, fg, bg, reverse=rng.random() < 0.1)
            c += n + rng.randint(1, 3)
    s.cursor = (rng.randrange(rows), rng.randrange(cols))
    s.mark_ambiguous(font)
    return s


def build(name, font, seed=0):
    if name == "alphabet":
        return alphabet(font)
    if name == "installer":
        return installer(font)
    if name.startswith("random"):
        return random_text(font, seed)
    raise ValueError("unknown screen %r (alphabet | installer | random)" % name)
