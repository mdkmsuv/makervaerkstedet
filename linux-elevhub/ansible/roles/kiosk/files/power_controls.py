"""Local power buttons. No browser endpoint, forced shutdown or file deletion."""
import queue
import subprocess
import threading
from update_status import STATUS_PATH, blocks_power, current_boot_id, message, read_status


HELPER = '/usr/local/sbin/elevhub-power'
ACTIONS = {'poweroff': ('Sluk', 'slukker'), 'reboot': ('Genstart', 'genstarter')}


def request_power(action, run=subprocess.run):
    if action not in ACTIONS:
        raise ValueError('Ukendt handling')
    run(['sudo', '-n', HELPER, action], check=True, capture_output=True, timeout=15)


class PowerControls:
    def __init__(self, root, parent, paused=lambda: False, before_power=None):
        import tkinter as tk
        self.tk, self.root, self.paused = tk, root, paused
        self.before_power = before_power
        self.dialog = None
        self.pending = False
        self.results = queue.Queue()
        self.frame = tk.Frame(parent, bg='white')
        # Pack before optional USB/battery information, so these stay visible.
        self.frame.pack(side='right', padx=(6, 10), fill='y', pady=8)
        self.buttons = []
        for action, (label, _) in ACTIONS.items():
            button = tk.Button(self.frame, text=label, command=lambda a=action: self.ask(a),
                               font=('Metrophobic', 12), bg='white', fg='#333333',
                               activebackground='#eaeaea', relief='solid', bd=1,
                               padx=10, cursor='hand2')
            button.pack(side='left', fill='y', padx=3)
            self.buttons.append(button)

    def ask(self, action):
        if action not in ACTIONS or self.paused() or self.pending:
            return
        status = read_status(STATUS_PATH, boot_id=current_boot_id())
        if blocks_power(status):
            self._show_blocked(message(status))
            return
        if self.dialog is not None:
            self.dialog.lift()
            return
        tk = self.tk
        label, verb = ACTIONS[action]
        dialog = self.dialog = tk.Toplevel(self.root)
        dialog.title(label + ' computeren')
        dialog.configure(bg='white')
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.attributes('-topmost', True)
        dialog.protocol('WM_DELETE_WINDOW', self.cancel)
        dialog.bind('<Escape>', lambda _event: self.cancel())
        frame = tk.Frame(dialog, bg='white', padx=26, pady=24)
        frame.pack(fill='both', expand=True)
        tk.Label(frame, text=label + ' computeren?', bg='white', fg='#222222',
                 font=('Metrophobic', 21), anchor='w').pack(fill='x')
        tk.Label(frame, text=f'Før computeren {verb}, afslutter vi dine åbne værksteder.\n'
                 'Du kan gemme dit arbejde eller vælge at arbejde videre.\n\n'
                 'Næste gang starter du på en frisk. Dine gemte filer bliver bevaret.',
                 bg='white', fg='#444444', font=('Metrophobic', 13), justify='left',
                 wraplength=480, anchor='w').pack(fill='x', pady=(14, 18))
        self.status = tk.Label(frame, text='', bg='white', fg='#b4121a',
                               font=('Metrophobic', 12), wraplength=480, justify='left')
        self.status.pack(fill='x')
        choices = tk.Frame(frame, bg='white')
        choices.pack(fill='x', pady=(8, 0))
        self.cancel_button = tk.Button(choices, text='Arbejd videre', command=self.cancel,
                                      font=('Metrophobic', 13), bg='#eaeaea', fg='#333333',
                                      relief='flat', padx=18, pady=10)
        self.cancel_button.pack(side='left')
        self.confirm_button = tk.Button(choices, text='Ja, ' + label.lower(),
                                       command=lambda: self.confirm(action),
                                       font=('Metrophobic', 13), bg='#b4121a', fg='white',
                                       activebackground='#920008', activeforeground='white',
                                       relief='flat', padx=18, pady=10)
        self.confirm_button.pack(side='right')
        dialog.update_idletasks()
        x = max(0, (dialog.winfo_screenwidth() - dialog.winfo_reqwidth()) // 2)
        y = max(60, (dialog.winfo_screenheight() - dialog.winfo_reqheight()) // 2)
        dialog.geometry(f'+{x}+{y}')
        dialog.grab_set()
        self.cancel_button.focus_set()

    def _show_blocked(self, text):
        tk = self.tk
        if self.dialog is not None:
            self.dialog.lift()
            return
        dialog = self.dialog = tk.Toplevel(self.root)
        dialog.title('Opdatering i gang')
        dialog.configure(bg='white')
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.attributes('-topmost', True)
        dialog.protocol('WM_DELETE_WINDOW', self.cancel)
        frame = tk.Frame(dialog, bg='white', padx=26, pady=24)
        frame.pack(fill='both', expand=True)
        tk.Label(frame, text='Vent med at slukke eller genstarte', bg='white', fg='#222222',
                 font=('Metrophobic', 21), anchor='w').pack(fill='x')
        tk.Label(frame, text=text or 'Opdateringen kører. Lad computeren være tændt.',
                 bg='white', fg='#b4121a', font=('Metrophobic', 13, 'bold'),
                 justify='left', wraplength=480, anchor='w').pack(fill='x', pady=(14, 18))
        self.cancel_button = tk.Button(frame, text='Luk besked', command=self.cancel,
                                       font=('Metrophobic', 13), bg='#eaeaea', fg='#333333',
                                       relief='flat', padx=18, pady=10)
        self.cancel_button.pack(anchor='e')
        dialog.update_idletasks()
        x = max(0, (dialog.winfo_screenwidth() - dialog.winfo_reqwidth()) // 2)
        y = max(60, (dialog.winfo_screenheight() - dialog.winfo_reqheight()) // 2)
        dialog.geometry(f'+{x}+{y}')
        dialog.grab_set()
        self.cancel_button.focus_set()

    def cancel(self):
        if self.pending:
            return
        if self.dialog is not None:
            self.dialog.grab_release()
            self.dialog.destroy()
            self.dialog = None

    def confirm(self, action):
        if self.pending or self.dialog is None:
            return
        if self.paused():
            self.cancel()
            return
        status = read_status(STATUS_PATH, boot_id=current_boot_id())
        if blocks_power(status):
            self.status.configure(text=message(status) or 'Opdateringen kører. Vent med at slukke eller genstarte.')
            return
        self.pending = True
        self.cancel_button.configure(state='disabled')
        self.confirm_button.configure(state='disabled')
        for button in self.buttons:
            button.configure(state='disabled')
        if self.before_power is not None:
            # A Tk grab/topmost confirmation must not cover the editor's
            # save/discard/continue dialog while the guardian is waiting.
            self.dialog.grab_release()
            self.dialog.withdraw()
            try:
                self.before_power(lambda: self._dispatch(action), reason='close', completed=self._guard_result)
            except Exception:
                self._guard_result({'state': 'error'})
            return
        self._dispatch(action)

    def _guard_result(self, result):
        if result.get('state') == 'completed':
            return
        self.pending = False
        self.cancel()
        for button in self.buttons:
            button.configure(state='normal')

    def _dispatch(self, action):
        self.dialog.deiconify()
        self.status.configure(text='Computeren ' + ACTIONS[action][1] + ' …')
        threading.Thread(target=self._request, args=(action,), daemon=True).start()
        self.root.after(100, self._result)

    def _request(self, action):
        try:
            request_power(action)
            self.results.put(True)
        except (OSError, subprocess.SubprocessError, ValueError):
            self.results.put(False)

    def _result(self):
        try:
            success = self.results.get_nowait()
        except queue.Empty:
            self.root.after(100, self._result)
            return
        if not success:
            self.pending = False
            self.status.configure(text='Computeren kunne ikke afslutte. Dine programmer er stadig åbne. '
                                  'Gå tilbage, og bed en voksen om hjælp.')
            self.cancel_button.configure(state='normal')
            self.confirm_button.configure(state='normal')
            for button in self.buttons:
                button.configure(state='normal')
