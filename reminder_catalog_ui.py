"""Tk controls for the catalog; only the UI thread touches widgets."""
import tkinter as tk
from tkinter import ttk, messagebox

import numpy as np
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
        ttk.Label(body, text='勾选后为每项设置区域；新增图标请在亮起时校准。修改设置会停止当前监测。').pack(anchor='w')
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
            ttk.Button(rows, text='设置检测区域', command=lambda s=skill: self.setup(s)).grid(row=row, column=2, padx=8)
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
        if skill.engine == 'legacy_loss':
            return '共用敌方 Buff 栏选区'
        if skill.engine == 'legacy_dizi':
            return '已设置' if self.app.dizi_detector.configured else '待设置'
        return '已校准' if self.engine.calibration(skill) else '待校准'

    def setup(self, skill):
        self.app.stop_monitor()
        if skill.engine == 'legacy_loss':
            self.app.select_roi()
            return
        if skill.engine == 'legacy_dizi':
            self.app.setup_dizi()
            return
        messagebox.showinfo('校准 '+skill.name,
                            '请让图标处于亮起状态。确定后 5 秒截图，紧贴完整图案框选，Enter 确认。\n'
                            '请避开倒计时数字和邻近图标。每项保存独立检测区域，位置变化后需要重新设置。', parent=self.app.root)
        if self.app.overlay is not None:
            self.app._hide_overlay(self.app.overlay)
        if self.dialog is not None:
            self.dialog.withdraw()
        self.app.root.iconify()
        self.app.root.after(5000, lambda: self.capture_setup(skill))

    def capture_setup(self, skill):
        try:
            shot = self.app.capture.full_screen()
            self.app.root.deiconify()
            def selected(box):
                try:
                    ox, oy = shot.info.get('screen_origin', (0, 0))
                    x0, y0, x1, y1 = box
                    rgb = np.asarray(shot.crop((x0-ox, y0-oy, x1-ox, y1-oy)).convert('RGB'))
                    value = self.engine.matchers[skill.id].calibrate(rgb, box)
                    value['icon'] = skill.icon
                    calibrations = dict(self.app.cfg.get('skill_calibrations', {}))
                    calibrations[skill.id] = value
                    self.app.cfg['skill_calibrations'] = calibrations
                    self.save(self.app.cfg)
                    messagebox.showinfo('校准完成', skill.name+' 已保存，可在监控列表勾选后开始监测。', parent=self.app.root)
                except (ValueError, OSError) as exc:
                    messagebox.showerror('校准失败', str(exc), parent=self.app.root)
            self.selector(self.app.root, shot, '框选亮起的 '+skill.name, selected,
                          min_w=6, min_h=6, confirm_on_enter=True)
        except Exception as exc:
            self.app.root.deiconify()
            messagebox.showerror('截图失败', str(exc), parent=self.app.root)
        # Reopening the list refreshes calibration labels and checkbox state.
        if self.dialog is not None:
            self.dialog.destroy()
            self.dialog = None
            self.labels = {}

    def validate_start(self):
        self.apply_defaults()
        enabled = [s for s in self.skills if self.engine.enabled(s)]
        if not enabled:
            raise ValueError('请在监控列表中至少勾选一项')
        missing = [s.name for s in enabled if s.engine == 'generic' and not self.engine.calibration(s)]
        if missing:
            raise ValueError('请先设置检测区域：'+ '、'.join(missing))
        self.engine.reset()

    def handle(self, results):
        alerts = []
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
            if result.get('event') == 'ready':
                alerts.append(skill.name+'可用')
            elif result.get('event') == 'expiring':
                alerts.append(f"{skill.name}还剩 {result['remaining']:.1f} 秒")
        if alerts:
            # Generic reminders use an electronic sound; never play another skill's voice.
            if self.app.cfg.get('audio_enabled', True):
                self.app._play_catalog_audio()
            self.app.show_visual_reminder('\n'.join(alerts))

    def icon_items(self, now, preview, preview_started):
        result = []
        for skill in self.skills:
            if skill.engine != 'generic' or not self.engine.enabled(skill):
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
