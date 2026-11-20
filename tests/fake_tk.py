import sys
import types

after_queue: list = []


class TclError(Exception):
    pass


class Var:
    def __init__(self, master=None, value=None):
        self._v = value if value is not None else self._default
        self._traces = []

    def get(self):
        return self._v

    def set(self, v):
        self._v = v
        for cb in self._traces:
            cb()

    def trace_add(self, mode, cb):
        self._traces.append(lambda: cb("", "", mode))


class StringVar(Var):
    _default = ""


class BooleanVar(Var):
    _default = False


class IntVar(Var):
    _default = 0


class Widget:
    def __init__(self, *args, **kw):
        self.master = args[0] if args and isinstance(args[0], Widget) else None
        self.kw = dict(kw)
        self.children_ = []
        self.destroyed = False
        self.bindings = {}
        if self.master is not None:
            self.master.children_.append(self)

    TK_METHODS = {
        "grid", "place", "place_forget", "grid_forget",
        "pack_propagate", "lift", "focus_set", "title", "geometry", "minsize", "resizable",
        "transient", "grab_set", "iconbitmap", "iconphoto", "option_add", "mainloop",
        "tk_popup", "add_command", "add", "column", "set", "theme_use", "map",
        "itemconfigure", "yview_moveto", "yview_scroll", "unbind_all",
        "update_idletasks", "columnconfigure", "rowconfigure", "select", "index", "tab",
        "deiconify", "focus_force", "attributes", "withdraw", "bind_class",
    }

    def __getattr__(self, name):
        if name in Widget.TK_METHODS:
            return lambda *a, **k: None
        raise AttributeError(f"{type(self).__name__} non ha l'attributo {name!r}")

    def configure(self, *style, **kw):
        self.kw.update(kw)

    config = configure

    def cget(self, key):
        return self.kw.get(key)

    def __getitem__(self, key):
        return self.kw.get(key)

    def winfo_exists(self):
        return not self.destroyed

    def winfo_children(self):
        return [c for c in self.children_ if not c.destroyed]

    def destroy(self):
        self.destroyed = True
        for c in self.children_:
            c.destroy()

    def bind(self, seq, fn=None, add=None):
        self.bindings[seq] = fn

    def pack(self, *a, **k):
        self.__dict__["packed"] = True

    def pack_forget(self):
        self.__dict__["packed"] = False

    def bind_all(self, seq, fn=None, add=None):
        self.bindings[seq] = fn

    def protocol(self, name, fn=None):
        self.bindings[name] = fn

    def after(self, ms, fn=None, *args):
        after_queue.append((fn, args))
        return f"after#{len(after_queue)}"

    def after_cancel(self, _id):
        pass

    clipboard = None

    def clipboard_get(self):
        if Widget.clipboard is None:
            raise TclError("vuoto")
        return Widget.clipboard

    def clipboard_clear(self):
        Widget.clipboard = ""

    def clipboard_append(self, text):
        Widget.clipboard = (Widget.clipboard or "") + text

    def yview(self, *a):
        return (0.0, 1.0)

    def bbox(self, *a):
        return (0, 0, 10, 10)

    def create_window(self, *a, **k):
        return 1

    def get_children(self, *a):
        return list(getattr(self, "_rows", {}).keys())

    def insert(self, parent, index, iid=None, **kw):
        self.__dict__.setdefault("_rows", {})[iid] = kw.get("values")
        self.__dict__.setdefault("_tags", {})[iid] = tuple(kw.get("tags") or ())
        self.__dict__.setdefault("_images", {})[iid] = kw.get("image")

    def heading(self, col, **kw):
        self.__dict__.setdefault("_headings", {}).setdefault(col, {}).update(kw)

    def exists(self, iid):
        return iid in self.__dict__.get("_rows", {})

    def selection_set(self, items):
        self.__dict__["_sel"] = tuple(items)

    def tag_configure(self, *a, **k):
        pass

    def click(self, row, column="#1", region="cell"):
        self.__dict__["_hit"] = (region, column, row)
        handler = self.bindings.get("<Button-1>")
        return handler(types.SimpleNamespace(x=1, y=1)) if handler else None

    def identify_region(self, x, y):
        return self.__dict__.get("_hit", ("nothing", "", ""))[0]

    def identify_column(self, x):
        return self.__dict__.get("_hit", ("", "", ""))[1]

    def identify_row(self, y):
        return self.__dict__.get("_hit", ("", "", ""))[2]

    def delete(self, *items):
        rows = self.__dict__.get("_rows", {})
        for i in items:
            rows.pop(i, None)

    def selection(self):
        return self.__dict__.get("_sel", ())


class Text(Widget):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.buf = ""

    def get(self, start, end):
        return self.buf + "\n"

    def insert(self, index, text, *tags):
        self.buf = text + self.buf if index == "1.0" else self.buf + text

    def delete(self, start, end=None):
        self.buf = ""

    def edit_modified(self, flag=None):
        return False


class Tk(Widget):
    def __init__(self, *a, **k):
        super().__init__()


class PhotoImage:
    def __init__(self, *a, **k):
        self.kw = k


def _mod(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


def install():
    names = ["Frame", "Label", "Button", "Canvas", "Toplevel", "Menu", "Checkbutton",
             "Radiobutton", "Entry", "Listbox", "Scrollbar"]
    tk = _mod("tkinter", Tk=Tk, TclError=TclError, StringVar=StringVar, BooleanVar=BooleanVar,
              IntVar=IntVar, PhotoImage=PhotoImage, Misc=Widget, Text=Text,
              **{n: type(n, (Widget,), {}) for n in names})
    ttk_names = ["Frame", "Label", "Button", "Entry", "Combobox", "Treeview", "Scrollbar",
                 "Progressbar", "Notebook", "Style"]
    ttk = _mod("tkinter.ttk", **{n: type(n, (Widget,), {}) for n in ttk_names})
    messages = []
    mb = _mod("tkinter.messagebox",
              showerror=lambda *a, **k: messages.append(("error",) + a),
              showinfo=lambda *a, **k: messages.append(("info",) + a),
              showwarning=lambda *a, **k: messages.append(("warn",) + a),
              askyesno=lambda *a, **k: True, messages=messages)
    fd = _mod("tkinter.filedialog", askdirectory=lambda **k: "",
              askopenfilenames=lambda **k: (), answers=[])
    fd.asksaveasfilename = lambda **k: fd.answers.pop(0) if fd.answers else ""
    fd.askopenfilename = lambda **k: fd.answers.pop(0) if fd.answers else ""
    sd = _mod("tkinter.simpledialog", answers=[])
    sd.askstring = lambda *a, **k: sd.answers.pop(0) if sd.answers else None
    tk.ttk, tk.messagebox, tk.filedialog, tk.simpledialog = ttk, mb, fd, sd
    sys.modules.update({"tkinter": tk, "tkinter.ttk": ttk, "tkinter.messagebox": mb,
                        "tkinter.filedialog": fd, "tkinter.simpledialog": sd})
    return tk
