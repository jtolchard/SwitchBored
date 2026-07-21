import time
import shlex
import tkinter
import threading

import customtkinter as ctk

from ui_components import ToolTip
from .service_status import PENDING, batch_status_command, parse_batch_states
from .ui_helpers import find_ui_root, schedule_on_ui_thread


class ServicesWindow(ctk.CTkToplevel):
    """Machines × services grid with live status pills and quick actions.

    Rows are machines with at least one tracked service; columns are the
    union of all tracked services. Each cell shows the service state and
    offers start/stop/restart on click. Status is fetched with one batched
    `systemctl show` call per machine, on open, after actions, and via the
    Refresh All button — there is no periodic polling.
    """

    def __init__(self, master, core):
        """Build the grid and start the initial status fetch."""
        super().__init__(master)
        self.core = core
        self._ui_root = find_ui_root(self)

        self.title("System Services Overview")
        self.transient(master)
        self.geometry(f"+{master.winfo_x() + 60}+{master.winfo_y() + 60}")

        self.machines = [
            m for m in core.settings.get("machines", [])
            if m.get("services") and m.get("address")
        ]

        # Union of services in first-seen order.
        self.services = []
        for m in self.machines:
            for s in m["services"]:
                s = s.strip()
                if s and s not in self.services:
                    self.services.append(s)

        if not self.machines:
            ctk.CTkLabel(
                self, text="No machines have tracked services configured.",
                text_color="gray"
            ).pack(padx=30, pady=30)
            return

        # Packed before the grid so the expanding grid can never squeeze it
        # out of the window; pack centres it horizontally.
        ctk.CTkButton(
            self, text="Refresh All", width=120,
            command=self.refresh_all
        ).pack(side="bottom", pady=(8, 14))

        self._grid_frame = grid_frame = ctk.CTkScrollableFrame(self, corner_radius=10, fg_color="#1a1a1a")
        grid_frame.pack(fill="both", expand=True, padx=20, pady=(15, 4))

        # Column headers (services).
        for col, service in enumerate(self.services, start=1):
            ctk.CTkLabel(
                grid_frame, text=service, font=("", 11, "bold"), text_color="#bbbbbb"
            ).grid(row=0, column=col, padx=12, pady=(8, 6))

        # One row per machine; a pill per tracked service.
        self._cells = {}
        for row, machine in enumerate(self.machines, start=1):
            name = f"{machine.get('icon', '💻')} {machine.get('name', machine['address'])}"
            ctk.CTkLabel(
                grid_frame, text=name, anchor="w", font=("", 12)
            ).grid(row=row, column=0, padx=(10, 16), pady=4, sticky="w")

            tracked = {s.strip() for s in machine["services"] if s.strip()}
            for col, service in enumerate(self.services, start=1):
                if service not in tracked:
                    ctk.CTkLabel(grid_frame, text="—", text_color="#3a3a3a").grid(row=row, column=col)
                    continue

                pill = ctk.CTkLabel(
                    grid_frame, text="●", font=("", 16),
                    text_color=PENDING[1], cursor="pointinghand"
                )
                pill.grid(row=row, column=col, padx=12, pady=4)
                tip = ToolTip(pill, "Checking…")
                pill.bind(
                    "<Button-1>",
                    lambda e, m=machine, s=service: self._show_action_menu(e, m, s),
                )
                self._cells[(id(machine), service)] = (pill, tip)

        # Size the window so the whole grid is visible without scrolling,
        # capped to the screen. The scrollable frame's own requested size is
        # its canvas viewport, so measure the inner grid content instead.
        self.update_idletasks()
        content_w = grid_frame.winfo_reqwidth()
        content_h = grid_frame.winfo_reqheight()
        chrome_w = 90    # window padding + scrollbar
        chrome_h = 110   # paddings + bottom refresh bar

        width = max(min(content_w + chrome_w, int(self.winfo_screenwidth() * 0.9)), 480)
        height = max(min(content_h + chrome_h, int(self.winfo_screenheight() * 0.85)), 240)

        # Keep the window fully on screen.
        x = min(master.winfo_x() + 60, self.winfo_screenwidth() - width - 20)
        y = min(master.winfo_y() + 60, self.winfo_screenheight() - height - 40)

        self.minsize(480, 240)
        self.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 20)}")

        self.refresh_all()

    # ------------------------------------------------------------------
    # Status fetching
    # ------------------------------------------------------------------

    @staticmethod
    def _admin_target(machine):
        """Return a machine copy that uses the admin user when one is set."""
        m_ref = machine.copy()
        m_ref["user"] = machine.get("admin_user") or machine.get("user")
        return m_ref

    def _set_cell(self, machine, service, label, color):
        """Update one status pill (from any thread)."""
        cell = self._cells.get((id(machine), service))
        if cell is None:
            return
        pill, tip = cell

        def apply():
            if self.winfo_exists() and pill.winfo_exists():
                pill.configure(text_color=color)
                tip.text = label

        schedule_on_ui_thread(self._ui_root, apply)

    def refresh_all(self):
        """Re-fetch the status of every machine's services."""
        for machine in self.machines:
            self.refresh_machine(machine)

    def refresh_machine(self, machine):
        """Fetch all of one machine's service states in a single SSH call."""
        services = [s.strip() for s in machine["services"] if s.strip()]
        if not services:
            return

        for service in services:
            self._set_cell(machine, service, "Checking…", PENDING[1])

        target = self._admin_target(machine)
        cmd = batch_status_command(services)

        def worker():
            ok, raw = self.core.run_ssh_command(target, cmd, timeout=8)
            states = parse_batch_states(ok, raw, services)
            for service, (label, color) in states.items():
                self._set_cell(machine, service, label, color)

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _show_action_menu(self, event, machine, service):
        """Show a start/stop/restart menu for one cell."""
        menu = tkinter.Menu(self, tearoff=0)
        name = machine.get("name", machine.get("address", ""))
        menu.add_command(label=f"{service} on {name}", state="disabled")
        menu.add_separator()
        for action in ("start", "stop", "restart"):
            menu.add_command(
                label=action.capitalize(),
                command=lambda a=action: self._run_action(machine, service, a),
            )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _run_action(self, machine, service, action):
        """Run a systemctl action, then refresh that machine's row."""
        self._set_cell(machine, service, f"{action}…", "#d48806")
        target = self._admin_target(machine)
        cmd = f"systemctl {action} {shlex.quote(service)}"

        def worker():
            self.core.run_ssh_command(target, cmd)
            time.sleep(1.2)

            # Tk must only be touched on the main thread.
            def follow_up():
                if self.winfo_exists():
                    self.refresh_machine(machine)

            schedule_on_ui_thread(self._ui_root, follow_up)

        threading.Thread(target=worker, daemon=True).start()
