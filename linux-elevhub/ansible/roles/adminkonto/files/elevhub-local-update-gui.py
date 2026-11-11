#!/usr/bin/python3
"""Small unprivileged Tk interface for fixed local administrator actions."""
import json
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk

HELPER = '/usr/local/sbin/elevhub-local-update'
INK = '#333333'
MUTED = '#666666'
THEMES = {
    'red': ('#c62b2f', '#8c1d24'),
    'yellow': ('#a36a00', '#704600'),
    'green': ('#28734e', '#1a5037'),
}


def main():
    root = tk.Tk()
    root.title('Makerværkstedet · lokal opdatering')
    root.geometry('720x560')
    root.minsize(620, 540)
    root.protocol('WM_DELETE_WINDOW', root.destroy)
    root.configure(bg='#f7f5f2')
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('TFrame', background='#f7f5f2')
    style.configure('TLabel', background='#f7f5f2', foreground=INK, font=('Metrophobic', 13))
    style.configure('Title.TLabel', font=('Metrophobic', 23, 'bold'))
    style.configure('Muted.TLabel', foreground=MUTED, font=('Metrophobic', 11))
    style.configure('TButton', font=('Metrophobic', 13), padding=(14, 16))
    style.configure('Accent.TButton', background=THEMES['red'][0], foreground='white')
    style.map('Accent.TButton', background=[('active', '#8c1d24'), ('disabled', '#c9a7a7')])

    frame = ttk.Frame(root, padding=28)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Opdater denne computer', style='Title.TLabel').pack(anchor='w')
    ttk.Label(frame, text='Lokal administrator · Elevhub', style='Muted.TLabel').pack(anchor='w', pady=(2, 22))

    installed = tk.StringVar(value='Henter…')
    available = tk.StringVar(value='Henter…')
    status = tk.StringVar(value='Kontrollerer forbindelse…')
    error = tk.StringVar(value='')
    ttk.Label(frame, text='Installeret version').pack(anchor='w')
    ttk.Label(frame, textvariable=installed, font=('Metrophobic', 16, 'bold')).pack(anchor='w', pady=(2, 14))
    ttk.Label(frame, text='Nyeste tilgængelige version').pack(anchor='w')
    ttk.Label(frame, textvariable=available, font=('Metrophobic', 16, 'bold')).pack(anchor='w', pady=(2, 14))
    ttk.Label(frame, text='Status').pack(anchor='w')
    ttk.Label(frame, textvariable=status, wraplength=650).pack(anchor='w', pady=(2, 8))
    ttk.Label(frame, textvariable=error, foreground='#a1262d', wraplength=650).pack(anchor='w')

    buttons = ttk.Frame(frame)
    status_buttons = ttk.Frame(buttons)
    status_buttons.pack(fill='x')
    install_buttons = ttk.Frame(buttons)
    install_buttons.pack(fill='x', pady=(10, 0))
    busy = {'value': False}
    install_ready = {'value': False}
    action_buttons = []
    restart_button = None

    def set_busy(value):
        busy['value'] = value
        for button in action_buttons:
            if button is restart_button:
                button.configure(state='disabled' if value or not install_ready['value'] else 'normal')
            else:
                button.configure(state='disabled' if value else 'normal')

    def show_result(action, result):
        if result.get('error'):
            error.set(result['error'])
            if action in ('state', 'check'):
                install_ready['value'] = result.get('install_ready') is True
                status.set(
                    'Elevhub er offline. Den godkendte, hentede opdatering kan stadig installeres.'
                    if install_ready['value'] else
                    'Elevhub er offline. Kontrollér netværket, og prøv igen.')
                if 'installed_version' in result:
                    installed.set(result['installed_version'])
                available.set('Ikke tilgængelig uden forbindelse')
            else:
                status.set('Handlingen blev ikke gennemført.')
                install_ready['value'] = False
            return
        error.set('')
        brand, darker = THEMES.get(result.get('theme'), THEMES['red'])
        style.configure('Accent.TButton', background=brand, foreground='white')
        style.map('Accent.TButton', background=[('active', darker), ('disabled', '#c9a7a7')])
        install_ready['value'] = (
            action in ('state', 'check') and result.get('install_ready') is True)
        if 'installed_version' in result:
            installed.set(result['installed_version'])
        candidate = result.get('available')
        available.set(candidate.get('version', 'Ingen nyere version') if candidate else 'Ingen nyere version')
        if action == 'restart':
            status.set('Computeren genstarter nu og installerer den godkendte opdatering.')
        else:
            job = result.get('job') or {}
            request = result.get('request') or {}
            current = job.get('status') or request.get('status')
            detail = job.get('error') or request.get('error')
            if isinstance(detail, str) and detail:
                error.set(detail[:4096])
            elif job.get('code') or request.get('code'):
                error.set('Fejlkode: ' + str(job.get('code') or request.get('code'))[:160])
            labels = {
                'pending': 'Opdateringen er bestilt og venter på behandling.',
                'preparing': 'Forbereder opdateringen.',
                'running': 'Opdateringen behandles.',
                'staged_awaiting_restart': 'Opdateringen er hentet og klar. Vælg “Genstart og installér”, når arbejdet er gemt.',
                'waiting_for_restart': 'Opdateringen er hentet og klar. Vælg “Genstart og installér”, når arbejdet er gemt.',
                'waiting_for_package': 'Venter på at opdateringspakken bliver hentet.',
                'blocked': 'Opdateringen er blokeret. Kontrollér status, og prøv igen.',
                'complete': 'Opdateringen er installeret.',
                'completed': 'Opdateringen er installeret.',
                'failed': 'Opdateringen fejlede. Du kan prøve igen.',
                'ready': 'Forbindelsen er klar. Vælg “Opdatér” for at forberede en opdatering.',
            }
            status.set(labels.get(current, 'Status kontrolleret.'))
            if action in ('update', 'retry') and current not in ('staged_awaiting_restart', 'complete'):
                status.set(status.get() + ' Du kan lukke vinduet; bestillingen fortsætter.')
        if action in ('state', 'check') and result.get('feed_state') in ('offline', 'error'):
            status.set('Elevhub kan ikke kontaktes lige nu. Kontrollér netværket, og prøv igen.')

    def run_action(action):
        if busy['value']:
            return
        set_busy(True)
        error.set('')
        status.set('Arbejder…')

        def worker():
            try:
                completed = subprocess.run(
                    ['sudo', '-n', HELPER, action],
                    capture_output=True, text=True, timeout=45, check=False)
                try:
                    result = json.loads(completed.stdout)
                except (ValueError, TypeError):
                    result = {'error': 'Opdateringshjælperen sendte et ugyldigt svar.'}
                if completed.returncode and not result.get('error'):
                    result = {'error': 'Opdateringshandlingen kunne ikke gennemføres.'}
            except (OSError, subprocess.TimeoutExpired):
                result = {'error': 'Opdateringshjælperen kunne ikke startes. Kontrollér lokal opsætning.'}
            try:
                root.after(0, lambda: finish(action, result))
            except tk.TclError:
                # Closing the window does not cancel the already submitted request.
                pass

        threading.Thread(target=worker, daemon=True).start()

    def finish(action, result):
        show_result(action, result)
        set_busy(False)

    def add_button(label, action, style_name=None, parent=None):
        button = ttk.Button(parent or status_buttons, text=label, style=style_name or 'TButton',
                            command=lambda: run_action(action))
        button.pack(side='left', padx=(0, 10))
        action_buttons.append(button)
        return button

    add_button('Kontrollér igen', 'check')
    add_button('Prøv igen', 'retry')
    add_button('Opdatér', 'update', 'Accent.TButton', install_buttons)

    def restart():
        if busy['value']:
            return
        if messagebox.askyesno('Genstart og installér',
                               'Gem først arbejdet. Computeren genstarter nu for at installere den godkendte opdatering. Fortsæt?'):
            run_action('restart')

    restart_button = ttk.Button(install_buttons, text='Genstart og installér', command=restart)
    restart_button.pack(side='right', padx=(10, 0))
    action_buttons.append(restart_button)
    restart_button.configure(state='disabled')
    ttk.Button(install_buttons, text='Log ud', command=root.destroy).pack(side='right')
    buttons.pack(side='bottom', fill='x', pady=(24, 0))

    def poll_status():
        if not busy['value']:
            run_action('state')
        try:
            root.after(15000, poll_status)
        except tk.TclError:
            pass

    root.after(100, lambda: run_action('state'))
    root.after(15000, poll_status)
    root.mainloop()


if __name__ == '__main__':
    main()
