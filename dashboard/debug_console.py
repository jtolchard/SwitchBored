import os
import socket
import threading

import customtkinter as ctk
from dashboard.ui_helpers import MAX_DEBUG_LINES

class DebugConsoleMixin:
    """Mixin providing the embedded dashboard debug console and its local socket server."""

    def setup_debug_console(self):
        """Create the debug console UI and start the local log socket server."""
        self.debug_frame = ctk.CTkFrame(
            self,
            height=120,
            corner_radius=8,
            fg_color="#111111",
            border_width=1,
            border_color="#333333"
        )
        self.debug_frame.grid_propagate(False)

        self.debug_textbox = ctk.CTkTextbox(
            self.debug_frame,
            font=("Courier", 12),
            text_color="#2fa572",
            fg_color="transparent",
            wrap="word"
        )
        self.debug_textbox.pack(fill="both", expand=True, padx=5, pady=5)

        self.core.log_callback = self.write_to_debug

        self._debug_server_stop = threading.Event()
        self._debug_socket_path = self.core.runtime_path("debug_console.sock")
        self._start_debug_log_server()

        self.core.log("SYSTEM", "--- SwitchBored Debug Console Initialized ---")

    def toggle_debug_console(self):
        """Show or hide the debug console based on the current settings."""
        if self.settings.get("debug_mode", False):
            self.debug_frame.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 10))
        else:
            self.debug_frame.grid_remove()

    def write_to_debug(self, message):
        """Schedule a thread-safe append of a debug message to the console.

        Called from the socket server thread and the monitor thread, so it
        must go through the main-thread UI queue rather than touching Tk.
        """
        self.ui_call(lambda: self._append_debug_text(message))

    def _append_debug_text(self, message):
        """Append text to the console while preserving user scroll position when possible."""
        if not self.winfo_exists():
            return

        _, last = self.debug_textbox.yview()
        user_at_bottom = last > 0.98

        self.debug_textbox.insert("end", message)

        lines = int(self.debug_textbox.index("end-1c").split(".")[0])
        if lines > MAX_DEBUG_LINES:
            self.debug_textbox.delete("1.0", f"{lines - MAX_DEBUG_LINES}.0")

        if user_at_bottom:
            self.debug_textbox.see("end")

    def _start_debug_log_server(self):
        """Start the local Unix socket server used to receive debug log messages."""
        def server_loop():
            """Listen for socket messages and forward them into the debug console."""
            try:
                if os.path.exists(self._debug_socket_path):
                    os.remove(self._debug_socket_path)
            except Exception:
                pass

            try:
                server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                server.bind(self._debug_socket_path)
                server.listen(5)
                server.settimeout(0.5)
            except OSError as e:
                # e.g. AF_UNIX path too long, or a permissions problem.
                # The console buffers its messages, so losing the socket
                # only means cross-process logs stay queued.
                self.core.log("SYSTEM", f"Debug log socket unavailable: {e}")
                return

            self._debug_server = server

            while not self._debug_server_stop.is_set():
                try:
                    conn, _ = server.accept()
                except socket.timeout:
                    continue
                except Exception:
                    break

                with conn:
                    try:
                        chunks = []
                        while True:
                            data = conn.recv(4096)
                            if not data:
                                break
                            chunks.append(data)
                        if chunks:
                            text = b"".join(chunks).decode("utf-8", errors="replace")
                            self.write_to_debug(text)
                    except Exception:
                        pass

            try:
                server.close()
            except Exception:
                pass

            try:
                if os.path.exists(self._debug_socket_path):
                    os.remove(self._debug_socket_path)
            except Exception:
                pass

        threading.Thread(target=server_loop, daemon=True).start()