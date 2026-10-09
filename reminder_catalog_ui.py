"""Tk controls for the catalog; only the UI thread touches widgets."""
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image

from reminder_catalog import CatalogMonitor, load_catalog


class CatalogUI:
    def __init__(self, app, root, save_config, selector):
        self.app, self.root, self.save, self.selector = app, root, save_config, selector
        self.skills = load_catalog(root)
        self.engine = CatalogMonitor(root, self.skills, app.cfg)
        self.icons = {s.id: Image.open(root/s.icon).convert('RGBA') for s in self.skills}
        self.labels = {}
        self.dialog = None
        self.apply_defaults()

    def apply_defaults(self):
        self.engine.cfg = self.app.cfg
        for skill in self.skills:
            if skill.engine == 'legacy_loss':
                self.app.cfg['loss_duration'] = skill.cd
            elif skill.engine == 'legacy_dizi':
                self.app.cfg['dizi_cooldown_seconds'] = skill.cd
        # Absence from the table also removes legacy monitors.
        if not any(s.engine == 'legacy_loss' for s in self.skills):
            self.app.cfg['loss_enabled'] = False
        if not any(s.engine == 'legacy_dizi' for s in self.skills):
            self.app.cfg['dizi_enabled'] = False

    def open(self):
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.lift()
            return
        win = self.dialog = tk.Toplevel(self.app.root)
        win.protocol('WM_DELETE_WINDOW', lambda: (win.destroy(), setattr(self, 'dialog', None)))
        win.title('监控列表 · 可同时勾选多项')
        body = ttk.Frame(win, padding=12)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='只需框选 Debuff 栏区和奇术区；按类别共享区域，技能可多选。').pack(anchor='w')
        regions = ttk.Frame(body)
        regions.pack(fill='x', pady=8)
        for category, name in (('debuff', 'Debuff 栏区'), ('qishu', '奇术区')):
            ttk.Button(regions, text='框选'+name, command=lambda c=category: self.setup_region(c)).pack(side='left', padx=6)
            ttk.Label(regions, text='已设置' if self.engine.region(category) else '待设置').pack(side='left', padx=6)
        canvas = tk.Canvas(body, width=720, height=360, highlightthickness=0)
        scroll = ttk.Scrollbar(body, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        canvas.pack(fill='both', expand=True)
        rows = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=rows, anchor='nw')
        rows.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
        self.labels = {}
        for row, skill in enumerate(self.skills):
            enabled = tk.BooleanVar(value=self.engine.enabled(skill))
            ttk.Checkbutton(rows, text=skill.name, variable=enabled,
                            command=lambda s=skill, v=enabled: self.set_enabled(s, v.get())).grid(row=row, column=0, sticky='w', pady=10)
            text = f'{skill.school} · {"持续" if skill.kind == "buff" else "冷却"} {skill.cd:g} 秒'
            ttk.Label(rows, text=text).grid(row=row, column=1, sticky='w', padx=12)
            ttk.Label(rows, text='Debuff 栏区' if skill.category == 'debuff' else '奇术区').grid(row=row, column=2, padx=8)
            label = ttk.Label(rows, text=self.status_text(skill), width=25)
            label.grid(row=row, column=3, sticky='w')
            self.labels[skill.id] = label

    def set_enabled(self, skill, enabled):
        self.app.stop_monitor()
        if skill.engine == 'legacy_loss':
            self.app.cfg['loss_enabled'] = enabled
        elif skill.engine == 'legacy_dizi':
            self.app.cfg['dizi_enabled'] = enabled
            self.app.dizi_enabled_var.set(enabled)
        else:
            values = dict(self.app.cfg.get('monitored_skills', {}))
            values[skill.id] = enabled
            self.app.cfg['monitored_skills'] = values
        self.save(self.app.cfg)
        self.engine.reset()

    def status_text(self, skill):
        return '已设置共享区域' if self.engine.region(skill.category) else '待框选共享区域'

    def setup_region(self, category):
        self.app.stop_monitor()
        name = 'Debuff 栏区' if category == 'debuff' else '奇术区'
        messagebox.showinfo('框选 '+name,
                            '确定后 5 秒冻结截图，请框住整个'+name+'，四周留少量余量。\n'
                            '该类别的所有勾选技能会在同一区域内搜索，无需逐个框选或校准。Enter 确认，Esc 取消。', parent=self.app.root)
        if self.app.overlay is not None:
            self.app._hide_overlay(self.app.overlay)
        if self.dialog is not None:
            self.dialog.destroy()
            self.dialog = None
            self.labels = {}
        self.app.root.iconify()
        self.app.root.after(5000, lambda: self.capture_region(category))

    def set_region(self, category, box):
        key = {'debuff': 'loss_roi', 'qishu': 'qishu_roi'}[category]
        self.app.cfg[key] = list(map(int, box))
        self.save(self.app.cfg)
        self.engine.reset()
        self.app._update_summary()

    def capture_region(self, category):
        try:
            shot = self.app.capture.full_screen()
            self.app.root.deiconify()
            name = 'Debuff 栏区' if category == 'debuff' else '奇术区'
            self.selector(self.app.root, shot, '框选整个 '+name,
                          lambda box: self.set_region(category, box),
                          min_w=43 if category == 'debuff' else 12,
                          min_h=63 if category == 'debuff' else 12,
                          confirm_on_enter=True)
        except Exception as exc:
            self.app.root.deiconify()
            messagebox.showerror('截图失败', str(exc), parent=self.app.root)

    def validate_start(self):
        self.apply_defaults()
        enabled = [s for s in self.skills if self.engine.enabled(s)]
        if not enabled:
            raise ValueError('请在监控列表中至少勾选一项')
        missing = [('Debuff 栏区' if c == 'debuff' else '奇术区') for c in ('debuff', 'qishu')
                   if any(s.category == c for s in enabled) and not self.engine.region(c)]
        if missing:
            raise ValueError('请先设置检测区域：'+ '、'.join(missing))
        self.engine.reset()

    def handle(self, results):
        alerts = []
        generic_audio = False
        by_id = {s.id: s for s in self.skills}
        for result in results:
            skill = by_id[result['id']]
            self.engine.latest[skill.id] = result
            if result.get('error'):
                text = result['error']
            elif not result.get('visible'):
                text = '图标不可见'
            elif result.get('ready'):
                text = '可用'
            elif result.get('remaining') is None:
                text = '等待确认起始时刻'
            elif result['remaining'] <= 0:
                text = '等待图标确认'
            else:
                text = f"{result['remaining']:.1f} 秒"
            label = self.labels.get(skill.id)
            if label is not None and label.winfo_exists():
                label.configure(text=text)
            if skill.engine == 'legacy_dizi':
                self.app.vars['dizi'].set(text)
                last_alert = result.get('last_alert')
                repeat = (None if last_alert is None or not result.get('ready') else
                          max(0, float(self.app.cfg.get('dizi_repeat_interval', 25))-(result['observed']-last_alert)))
                self.app.vars['dizi_repeat'].set('-' if repeat is None else f'{repeat:.1f} s')
                if result.get('event') in ('ready', 'repeat'):
                    self.app._dizi_audio()
                    alerts.append(self.app._reminder_text('dizi', 0))
                continue
            if result.get('event') == 'ready':
                alerts.append(skill.name+'可用')
                generic_audio = True
            elif result.get('event') == 'expiring':
                alerts.append(f"{skill.name}还剩 {result['remaining']:.1f} 秒")
                generic_audio = True
        if alerts:
            # Generic reminders use an electronic sound; never play another skill's voice.
            if generic_audio and self.app.cfg.get('audio_enabled', True):
                self.app._play_catalog_audio()
            self.app.show_visual_reminder('\n'.join(alerts))

    def icon_items(self, now, preview, preview_started):
        result = []
        for skill in self.skills:
            if skill.engine == 'legacy_loss' or not self.engine.enabled(skill):
                continue
            state = self.engine.latest.get(skill.id, {})
            remaining = state.get('remaining')
            if remaining is not None:
                remaining = max(0, remaining-(now-state.get('observed', now)))
            visible = state.get('visible', False) and now-state.get('observed', 0) <= 2
            if preview:
                remaining = skill.cd-((now-preview_started) % skill.cd)
                visible = True
            result.append((self.icons[skill.id], remaining, skill.cd, state.get('ready', False), visible))
        return result
