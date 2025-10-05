import json
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from pynput import mouse, keyboard
from pynput.mouse import Controller as MouseController
from pynput.keyboard import Controller as KeyboardController, Key
from contextlib import contextmanager

# ---------------- RecorderPlayer ----------------
class RecorderPlayer:
    def __init__(self):
        self.recording = False
        self.playing = False
        self.start_time = None
        self.events = []  # список dict событий
        self.mouse_listener = None
        self.kb_listener = None
        self.play_thread = None
        self.stop_play_event = threading.Event()
        self.mouse_ctrl = MouseController()
        self.kb_ctrl = KeyboardController()

        # suppression for distinguishing synthetic events
        self._suppress_lock = threading.Lock()
        self._suppress_count = 0

    @contextmanager
    def _suppress_events(self):
        with self._suppress_lock:
            self._suppress_count += 1
        try:
            yield
        finally:
            with self._suppress_lock:
                self._suppress_count -= 1

    def is_suppressed(self):
        with self._suppress_lock:
            return self._suppress_count > 0

    # ---------------- Recording ----------------
    def _time(self):
        return time.time() - self.start_time if self.start_time else 0

    def start_recording(self):
        if self.playing:
            raise RuntimeError("Нельзя записывать во время воспроизведения")
        self.events = []
        self.start_time = time.time()
        self.recording = True

        def on_move(x, y):
            if self.recording:
                self.events.append({"type": "mouse_move", "time": self._time(), "x": x, "y": y})

        def on_click(x, y, button, pressed):
            if self.recording:
                self.events.append({
                    "type": "mouse_click", "time": self._time(),
                    "x": x, "y": y, "button": str(button), "pressed": bool(pressed)
                })

        def on_scroll(x, y, dx, dy):
            if self.recording:
                self.events.append({
                    "type": "mouse_scroll", "time": self._time(),
                    "x": x, "y": y, "dx": dx, "dy": dy
                })

        def on_press(key):
            if self.recording:
                # char if available, else string like "Key.space"
                k = getattr(key, "char", str(key))
                self.events.append({"type": "key_press", "time": self._time(), "key": k})

        def on_release(key):
            if self.recording:
                k = getattr(key, "char", str(key))
                self.events.append({"type": "key_release", "time": self._time(), "key": k})

        self.mouse_listener = mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll)
        self.kb_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self.mouse_listener.start()
        self.kb_listener.start()

    def stop_recording(self):
        if not self.recording:
            return
        self.recording = False
        time.sleep(0.05)
        if self.mouse_listener:
            try: self.mouse_listener.stop()
            except: pass
            self.mouse_listener = None
        if self.kb_listener:
            try: self.kb_listener.stop()
            except: pass
            self.kb_listener = None
        self.start_time = None

    # ---------------- Save/Load ----------------
    def save(self, filepath):
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.events, f, ensure_ascii=False, indent=2)

    def load(self, filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            self.events = json.load(f)

    # ---------------- Playback ----------------
    def play(self, repeat_count=1, interval=0):
        if self.recording:
            raise RuntimeError("Нельзя воспроизводить во время записи")
        if not self.events:
            raise RuntimeError("Нет записанных событий")
        if self.playing:
            return

        self.stop_play_event.clear()
        self.playing = True

        def target():
            try:
                cycles_done = 0
                infinite = (repeat_count <= 0)
                while infinite or cycles_done < repeat_count:
                    base_time = None
                    for ev in self.events:
                        if self.stop_play_event.is_set():
                            return
                        # compute target time relative to start of cycle
                        if base_time is None:
                            base_time = time.time() - ev.get("time", 0)
                        target_time = base_time + ev.get("time", 0)
                        # wait responsively
                        while True:
                            if self.stop_play_event.is_set():
                                return
                            now = time.time()
                            remaining = target_time - now
                            if remaining <= 0:
                                break
                            time.sleep(min(0.01, remaining))
                        # perform event (suppress listener while performing)
                        try:
                            self._perform_event(ev)
                        except Exception:
                            # ignore problems per-event to not kill whole playback
                            pass
                    cycles_done += 1
                    # wait interval between cycles in small chunks
                    if interval > 0:
                        endt = time.time() + interval
                        while time.time() < endt:
                            if self.stop_play_event.is_set():
                                return
                            time.sleep(0.05)
            finally:
                self.playing = False

        self.play_thread = threading.Thread(target=target, daemon=True)
        self.play_thread.start()

    def stop_play(self):
        # signal stop
        self.stop_play_event.set()
        # join only if caller is not playback thread
        if self.play_thread and threading.current_thread() != self.play_thread:
            try:
                self.play_thread.join(timeout=1.0)
            except RuntimeError:
                pass
        self.playing = False
        # clear event so next play can run normally
        self.stop_play_event.clear()

    def _perform_event(self, ev):
        t = ev.get("type")
        if t == "mouse_move":
            with self._suppress_events():
                x, y = ev.get("x"), ev.get("y")
                if x is not None and y is not None:
                    self.mouse_ctrl.position = (x, y)
        elif t == "mouse_click":
            x, y = ev.get("x"), ev.get("y")
            btn_str = ev.get("button", "")
            pressed = ev.get("pressed", True)
            name = btn_str.split(".")[-1] if btn_str else "left"
            btn_obj = getattr(mouse.Button, name, mouse.Button.left)
            with self._suppress_events():
                if x is not None and y is not None:
                    self.mouse_ctrl.position = (x, y)
                if pressed:
                    self.mouse_ctrl.press(btn_obj)
                else:
                    self.mouse_ctrl.release(btn_obj)
        elif t == "mouse_scroll":
            x, y = ev.get("x"), ev.get("y")
            dx, dy = ev.get("dx", 0), ev.get("dy", 0)
            with self._suppress_events():
                if x is not None and y is not None:
                    self.mouse_ctrl.position = (x, y)
                self.mouse_ctrl.scroll(dx, dy)
        elif t == "key_press":
            k = ev.get("key")
            self._press_key_from_string(k)
        elif t == "key_release":
            k = ev.get("key")
            self._release_key_from_string(k)

    def _press_key_from_string(self, k):
        if not k:
            return
        # k may be a single char like 'a' or "Key.space" or "'a'"
        if isinstance(k, str) and k.startswith("Key."):
            try:
                key_obj = getattr(Key, k.split(".", 1)[1])
                with self._suppress_events():
                    self.kb_ctrl.press(key_obj)
                return
            except Exception:
                pass
        # strip quotes if recorded as "'a'"
        if isinstance(k, str) and len(k) >= 2 and k[0] == "'" and k[-1] == "'":
            ch = k[1:-1]
            with self._suppress_events():
                try:
                    self.kb_ctrl.press(ch)
                except Exception:
                    pass
            return
        # normal char
        try:
            with self._suppress_events():
                self.kb_ctrl.press(k)
        except Exception:
            pass

    def _release_key_from_string(self, k):
        if not k:
            return
        if isinstance(k, str) and k.startswith("Key."):
            try:
                key_obj = getattr(Key, k.split(".", 1)[1])
                with self._suppress_events():
                    self.kb_ctrl.release(key_obj)
                return
            except Exception:
                pass
        if isinstance(k, str) and len(k) >= 2 and k[0] == "'" and k[-1] == "'":
            ch = k[1:-1]
            with self._suppress_events():
                try:
                    self.kb_ctrl.release(ch)
                except Exception:
                    pass
            return
        try:
            with self._suppress_events():
                self.kb_ctrl.release(k)
        except Exception:
            pass


# ---------------- GUI / App ----------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("Автокликер — Запись и Воспроизведение")
        root.geometry("620x360")
        root.resizable(False, False)

        self.rp = RecorderPlayer()

        # theme colors
        self.dark = True
        self.bg_dark = "#2b2b2b"
        self.fg_dark = "#ffffff"
        self.btn_bg_dark = "#3a3a3a"

        self.bg_light = "#f0f0f0"
        self.fg_light = "#000000"
        self.btn_bg_light = "#e0e0e0"

        # variables
        self.status_var = tk.StringVar(value="Ожидание")
        self.interval_var = tk.StringVar(value="1.0")
        self.repeat_var = tk.StringVar(value="1")
        self.infinite_var = tk.IntVar(value=0)
        self.events_count_var = tk.StringVar(value="Событий: 0")

        # build UI with grid
        self._build_ui()

        # global listeners (keyboard + mouse) — always running
        self.global_k_listener = keyboard.Listener(
            on_press=self._on_global_key_press,
            on_release=self._on_global_key_release
        )
        self.global_m_listener = mouse.Listener(
            on_move=self._on_global_mouse_move,
            on_click=self._on_global_mouse_click,
            on_scroll=self._on_global_mouse_scroll
        )
        self.global_k_listener.start()
        self.global_m_listener.start()

        # ui updater
        self._ui_updater()

    def _build_ui(self):
        # theme button top-right
        self.theme_btn = tk.Button(self.root, text="🌙", command=self.toggle_theme, font=("Segoe UI", 11), bd=0)
        self.theme_btn.grid(row=0, column=3, sticky="e", padx=10, pady=8)

        tk.Label(self.root, text="Статус:").grid(row=0, column=0, sticky="w", padx=8, pady=8)
        tk.Label(self.root, textvariable=self.status_var, width=44, anchor="w").grid(row=0, column=1, columnspan=2, sticky="w")

        # row 1 - recording buttons
        self.btn_start_rec = tk.Button(self.root, text="Начать запись", width=20, command=self.start_recording)
        self.btn_stop_rec = tk.Button(self.root, text="Остановить запись", width=20, command=self.stop_recording, state="disabled")
        self.btn_save = tk.Button(self.root, text="Сохранить .rec", width=20, command=self.save_file, state="disabled")

        self.btn_start_rec.grid(row=1, column=0, padx=8, pady=6)
        self.btn_stop_rec.grid(row=1, column=1, padx=8, pady=6)
        self.btn_save.grid(row=1, column=2, padx=8, pady=6)

        # row 2 - playback buttons
        self.btn_load = tk.Button(self.root, text="Загрузить .rec", width=20, command=self.load_file)
        self.btn_play = tk.Button(self.root, text="▶ Воспроизвести (F8)", width=20, command=self.play, state="disabled")
        self.btn_stop_play = tk.Button(self.root, text="■ Остановить", width=20, command=self.stop_play, state="disabled")

        self.btn_load.grid(row=2, column=0, padx=8, pady=6)
        self.btn_play.grid(row=2, column=1, padx=8, pady=6)
        self.btn_stop_play.grid(row=2, column=2, padx=8, pady=6)

        # row 3-4 settings
        tk.Label(self.root, text="Интервал (сек):").grid(row=3, column=0, sticky="e", padx=8, pady=6)
        tk.Entry(self.root, textvariable=self.interval_var, width=12).grid(row=3, column=1, sticky="w")

        tk.Label(self.root, text="Повтор (0 = бесконечно):").grid(row=4, column=0, sticky="e", padx=8, pady=6)
        tk.Entry(self.root, textvariable=self.repeat_var, width=12).grid(row=4, column=1, sticky="w")
        tk.Checkbutton(self.root, text="Бесконечно", variable=self.infinite_var).grid(row=4, column=2, sticky="w")

        # events count & hint
        tk.Label(self.root, textvariable=self.events_count_var).grid(row=5, column=0, columnspan=3, sticky="w", padx=8, pady=8)
        tk.Label(self.root, text="F8 — запуск / стоп воспроизведения; любое вмешательство пользователя при воспроизведении прерывает его.").grid(row=6, column=0, columnspan=3, sticky="w", padx=8, pady=6)

        # apply initial theme
        self.apply_theme()

    def apply_theme(self):
        if self.dark:
            bg, fg, btn_bg = self.bg_dark, self.fg_dark, self.btn_bg_dark
            self.theme_btn.config(text="🌙")
        else:
            bg, fg, btn_bg = self.bg_light, self.fg_light, self.btn_bg_light
            self.theme_btn.config(text="☀️")

        try:
            self.root.configure(bg=bg)
        except:
            pass

        for w in self.root.winfo_children():
            cls = w.winfo_class()
            try:
                if cls == "Entry":
                    w.configure(bg=btn_bg, fg=fg, insertbackground=fg)
                elif cls == "Button":
                    w.configure(bg=btn_bg, fg=fg, activebackground=bg, activeforeground=fg)
                elif cls == "Label":
                    w.configure(bg=bg, fg=fg)
                elif cls == "Checkbutton":
                    w.configure(bg=bg, fg=fg, selectcolor=bg, activebackground=bg)
                else:
                    # generic attempt
                    w.configure(bg=bg, fg=fg)
            except tk.TclError:
                # some widgets may not accept bg/fg — ignore
                pass

    def toggle_theme(self):
        self.dark = not self.dark
        self.apply_theme()

    # ----------------- global listeners (detect user interference & F8) -----------------
    def _on_global_key_press(self, key):
        # F8 toggles playback (start/stop)
        try:
            if key == Key.f8:
                # toggle playback
                if self.rp.playing:
                    # call stop_play on main thread to avoid tkinter threading issues
                    self.root.after(0, self.stop_play)
                else:
                    self.root.after(0, self.play)
                return
        except Exception:
            pass

        # any other key while playing and NOT suppressed -> treat as user intervention -> stop
        if self.rp.playing and (not self.rp.is_suppressed()):
            self.root.after(0, self.stop_play)

    def _on_global_key_release(self, key):
        # we don't need special handling on release, but keep to avoid listener closure
        return

    def _on_global_mouse_move(self, x, y):
        if self.rp.playing and (not self.rp.is_suppressed()):
            self.root.after(0, self.stop_play)

    def _on_global_mouse_click(self, x, y, button, pressed):
        if self.rp.playing and (not self.rp.is_suppressed()):
            self.root.after(0, self.stop_play)

    def _on_global_mouse_scroll(self, x, y, dx, dy):
        if self.rp.playing and (not self.rp.is_suppressed()):
            self.root.after(0, self.stop_play)

    # ----------------- UI actions -----------------
    def _ui_updater(self):
        # events count + buttons states
        self.events_count_var.set(f"Событий: {len(self.rp.events)}")
        self.btn_play.config(state="normal" if self.rp.events and not self.rp.playing else "disabled")
        self.btn_stop_play.config(state="normal" if self.rp.playing else "disabled")
        self.btn_stop_rec.config(state="normal" if self.rp.recording else "disabled")
        self.btn_save.config(state="normal" if self.rp.events and not self.rp.recording else "disabled")
        # status
        if self.rp.recording:
            self.status_var.set("Запись...")
        elif self.rp.playing:
            self.status_var.set("Воспроизведение...")
        else:
            self.status_var.set("Ожидание")
        self.root.after(150, self._ui_updater)

    def start_recording(self):
        try:
            self.rp.start_recording()
            # ui updates handled by _ui_updater
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось начать запись:\n{e}")

    def stop_recording(self):
        self.rp.stop_recording()
        self.events_count_var.set(f"Событий: {len(self.rp.events)}")
        messagebox.showinfo("Готово", f"Запись завершена. Событий: {len(self.rp.events)}")

    def save_file(self):
        if not self.rp.events:
            messagebox.showwarning("Нет событий", "Нет записанных событий для сохранения.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".rec", filetypes=[("REC файлы", "*.rec"), ("JSON", "*.json")])
        if not path:
            return
        try:
            self.rp.save(path)
            messagebox.showinfo("Сохранено", f"Сохранено {len(self.rp.events)} событий в:\n{path}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить:\n{e}")

    def load_file(self):
        path = filedialog.askopenfilename(filetypes=[("REC файлы", "*.rec"), ("JSON", "*.json")])
        if not path:
            return
        try:
            self.rp.load(path)
            self.events_count_var.set(f"Событий: {len(self.rp.events)}")
            messagebox.showinfo("Загружено", f"Загружено {len(self.rp.events)} событий из:\n{path}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось загрузить:\n{e}")

    def play(self):
        if self.rp.recording:
            messagebox.showwarning("Нельзя", "Сначала остановите запись.")
            return
        if not self.rp.events:
            messagebox.showwarning("Нет данных", "Нет событий для воспроизведения.")
            return
        try:
            interval = float(self.interval_var.get() or "0")
        except Exception:
            messagebox.showerror("Ошибка", "Некорректное значение интервала.")
            return
        try:
            repeat = int(self.repeat_var.get() or "1")
        except Exception:
            repeat = 1
        if self.infinite_var.get():
            repeat = 0
        # start playback
        try:
            self.rp.play(repeat_count=repeat, interval=interval)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось запустить воспроизведение:\n{e}")

    def stop_play(self):
        # stop playback
        self.rp.stop_play()
        messagebox.showinfo("Остановлено", "Воспроизведение прервано.")


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
    