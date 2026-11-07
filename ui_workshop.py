"""Save browsing, change review, accessibility and batch editing for the workshop."""
import os
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

from workshop_paths import app_store, program_directory
from workshop_tools import (CHARACTER_PRESETS, FONT_SCALES,
                            list_save_files, load_preferences, restore_backup_copy, save_preferences)


class WorkshopFeatures:
    def _load_workshop_preferences(self):
        directory = getattr(self.mc, 'STORE', None) or app_store()
        self.preferences_path = Path(directory) / 'workshop_ui.json'
        self.preferences = load_preferences(self.preferences_path)
        self._widget_fonts = {}
        self._style_fonts = {}
        return self.preferences

    def _save_workshop_preferences(self, **values):
        self.preferences.update(values)
        try:
            save_preferences(self.preferences_path, self.preferences)
        except OSError:
            self.say('本次设置已生效；暂时无法保存偏好设置。')

    def _remember_save(self, path):
        recent = self.preferences.get('recent_saves', [])
        recent = recent if isinstance(recent, list) else []
        path = os.path.abspath(path)
        recent = [old for old in recent if isinstance(old, str) and os.path.normcase(old) != os.path.normcase(path)]
        self._save_workshop_preferences(recent_saves=[path, *recent][:8])

    def _build_quick_tools(self):
        self.quick_tools = ttk.Frame(self.main, padding=(24, 0, 24, 12))
        self.quick_tools.pack(fill='x')
        for text, command in [('存档列表', self.show_save_browser), ('剧情跳转', self.show_story_navigation),
                              ('修改预览', self.show_change_review),
                              ('撤销全部', self.discard_all_changes), ('备份记录', self.show_backups)]:
            button = ttk.Button(self.quick_tools, text=text, command=command, padding=(9, 7))
            button.pack(side='left', padx=(0, 7))
            if text == '修改预览':
                self.review_button = button
            elif text == '撤销全部':
                self.discard_button = button
        initial = self.preferences.get('font_size', '舒适')
        self.font_size = tk.StringVar(value=initial if initial in FONT_SCALES else '舒适')
        box = ttk.Combobox(self.quick_tools, textvariable=self.font_size, values=list(FONT_SCALES),
                           state='readonly', width=5)
        box.pack(side='right')
        box.bind('<<ComboboxSelected>>', lambda event: self.apply_font_size())
        ttk.Label(self.quick_tools, text='字号', style='Muted.TLabel').pack(side='right', padx=(0, 7))
        self.root.bind('<Control-l>', lambda event: self.show_save_browser())
        self.root.bind('<Control-p>', lambda event: self.show_change_review())
        self.root.bind('<Control-b>', lambda event: self.show_backups())
        self.root.bind('<Control-Shift-Z>', lambda event: self.discard_all_changes())

    def _font_tuple(self, description):
        actual = tkfont.Font(root=self.root, font=description).actual()
        if str(description) in ('TkDefaultFont', 'TkTextFont'):
            actual.update(family='Microsoft YaHei UI', size=10)
        return actual['family'], actual['size'], actual['weight'], actual['slant']

    def apply_font_size(self, persist=True):
        scale = FONT_SCALES.get(self.font_size.get(), 1.15)
        styles = ['.', 'Muted.TLabel', 'PageMuted.TLabel', 'PageTitle.TLabel', 'CardTitle.TLabel',
                  'Strong.TLabel', 'Accent.TLabel', 'Badge.TLabel', 'TButton', 'Primary.TButton',
                  'Nav.TButton', 'ActiveNav.TButton', 'Treeview', 'Treeview.Heading']
        for name in styles:
            if name not in self._style_fonts:
                value = self.style.configure(name).get('font') if self.style.configure(name) else None
                if value:
                    self._style_fonts[name] = self._font_tuple(value)
        for name, (family, size, weight, slant) in self._style_fonts.items():
            self.style.configure(name, font=(family, round(size * scale), weight, slant))
        self.style.configure('Treeview', rowheight=round(35 * scale))
        def visit(widget):
            try:
                if 'font' in widget.keys() and str(widget.cget('font')):
                    if widget not in self._widget_fonts:
                        self._widget_fonts[widget] = self._font_tuple(widget.cget('font'))
                    family, size, weight, slant = self._widget_fonts[widget]
                    widget.configure(font=(family, round(size * scale), weight, slant))
                for child in widget.winfo_children():
                    visit(child)
            except tk.TclError:
                return
        visit(self.root)
        self._widget_fonts = {widget: spec for widget, spec in self._widget_fonts.items() if widget.winfo_exists()}
        if persist:
            self._save_workshop_preferences(font_size=self.font_size.get())
            self.say('字号已调整为「%s」并保存' % self.font_size.get())

    def _dialog(self, title, width=960, height=560):
        window = tk.Toplevel(self.root)
        window.title(title)
        window.configure(bg='#F3F6FA')
        window.geometry('%dx%d' % (min(width, self.root.winfo_screenwidth() - 80),
                                     min(height, self.root.winfo_screenheight() - 100)))
        window.minsize(720, 420)
        window.transient(self.root)
        window.bind('<Escape>', lambda event: window.destroy())
        return window

    def _finish_dialog(self, window):
        self.apply_font_size(persist=False)
        def grab():
            if window.winfo_exists() and window.winfo_viewable():
                window.grab_set()
        window.after_idle(grab)

    def _dialog_table(self, parent, columns, widths):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=[name for name, _ in columns], show='headings', selectmode='extended')
        for (name, label), width in zip(columns, widths):
            tree.heading(name, text=label, anchor='w')
            tree.column(name, width=width, minwidth=55, stretch=True, anchor='w')
        tree.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(frame, command=tree.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree.tag_configure('invalid', foreground='#B44632')
        tree.tag_configure('pending', foreground='#106B5D')
        return tree

    def _save_folders(self):
        candidates = []
        if self.path:
            candidates.append(Path(self.path).parent)
        game = self.game_path.get().strip().strip('"')
        if game:
            candidates.append(Path(game) / 'Save')
        recent = self.preferences.get('recent_saves', [])
        if isinstance(recent, list):
            candidates.extend(Path(path).parent for path in recent if isinstance(path, str))
        candidates.append(program_directory() / 'Save')
        found = []
        for folder in candidates:
            try:
                folder = str(folder.resolve())
                if Path(folder).is_dir() and os.path.normcase(folder) not in [os.path.normcase(p) for p in found]:
                    found.append(folder)
            except OSError:
                continue
        return found

    def show_save_browser(self):
        return self._show_file_browser(False)

    def show_backups(self):
        return self._show_file_browser(True)

    def _show_file_browser(self, backups):
        window = self._dialog('备份记录' if backups else '存档列表')
        outer = ttk.Frame(window, padding=20)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='从备份恢复到一个新槽位' if backups else '找到存档，继续编辑', style='CardTitle.TLabel').pack(anchor='w')
        ttk.Label(outer, text='保留已有进度，恢复出的新槽位可直接在游戏中读取。' if backups else '按最近更新时间排列，双击条目即可打开。',
                  style='Muted.TLabel').pack(anchor='w', pady=(6, 14))
        bar = ttk.Frame(outer)
        bar.pack(fill='x', pady=(0, 12))
        folders = self._save_folders()
        folder = tk.StringVar(value=folders[0] if folders else '')
        box = ttk.Combobox(bar, textvariable=folder, values=folders, state='readonly')
        box.pack(side='left', fill='x', expand=True)
        records = {}
        tree = self._dialog_table(outer, [('slot', '原槽位' if backups else '槽位'), ('file', '备份文件' if backups else '存档文件'),
                                                ('updated', '更新时间'), ('size', '大小')], [80, 390, 190, 90])
        tree.configure(selectmode='browse')
        count = tk.StringVar()
        def refresh():
            tree.delete(*tree.get_children())
            records.clear()
            for i, row in enumerate(list_save_files(folder.get(), backups)):
                item = str(i)
                records[item] = row
                tree.insert('', 'end', iid=item, values=(f'データ{row["slot"]}', row['name'], row['updated'], f'{row["size"] / 1024:.1f} KB'))
            if records:
                preferred = self.path or next(iter(self.preferences.get('recent_saves', [])), '')
                selected_item = next((item for item, row in records.items() if os.path.normcase(row['path']) == os.path.normcase(preferred)), '0')
                tree.selection_set(selected_item)
                tree.see(selected_item)
            count.set('共 %d 份%s' % (len(records), '备份' if backups else '存档') if records else '没有找到自动备份，可选择存档所在的 Save 文件夹。' if backups else '没有找到存档，请选择游戏的 Save 文件夹。')
        def choose_folder():
            chosen = filedialog.askdirectory(parent=window, title='选择 Save 文件夹', initialdir=folder.get() or None)
            if chosen:
                if (Path(chosen) / 'Save').is_dir():
                    chosen = str(Path(chosen) / 'Save')
                box['values'] = list(dict.fromkeys([chosen, *box['values']]))
                folder.set(chosen)
                refresh()
        def selected():
            selection = tree.selection()
            return records.get(selection[0]) if selection else None
        def open_selected(event=None):
            row = selected()
            if row and self.open_save(row['path']):
                window.destroy()
        def restore_selected():
            row = selected()
            if not row:
                return
            if self.drafts and not messagebox.askyesno('切换到恢复的存档', '当前还有未生成的修改，是否放弃并载入恢复的新存档？', parent=window):
                return
            try:
                def validate(raw):
                    self.core.parse_all(self.core.decode_save(raw))
                out = restore_backup_copy(row['path'], folder.get(), validate)
                self.open_save(out, ask_discard=False)
                self.say('已从备份恢复到新槽位 ' + os.path.basename(out))
                window.destroy()
                messagebox.showinfo('恢复完成', '已生成 %s。\n在游戏中读取对应槽位即可。' % os.path.basename(out), parent=self.root)
            except Exception as exc:
                messagebox.showerror('无法恢复备份', str(exc), parent=window)
        ttk.Button(bar, text='选择文件夹', command=choose_folder).pack(side='left', padx=(9, 0))
        ttk.Button(bar, text='刷新', command=refresh).pack(side='left', padx=(8, 0))
        box.bind('<<ComboboxSelected>>', lambda event: refresh())
        footer = ttk.Frame(outer)
        footer.pack(side='bottom', fill='x', pady=(13, 0), before=tree.master)
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=count, style='Muted.TLabel', wraplength=820).grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 9))
        if backups:
            ttk.Button(footer, text='恢复到新槽位', style='Primary.TButton', command=restore_selected).grid(row=1, column=2)
        else:
            ttk.Button(footer, text='打开所选存档', style='Primary.TButton', command=open_selected).grid(row=1, column=2)
        ttk.Button(footer, text='打开文件夹', command=lambda: self._open_folder(folder.get())).grid(row=1, column=1, padx=8)
        ttk.Button(footer, text='关闭', command=window.destroy).grid(row=1, column=0, sticky='w')
        tree.bind('<Double-1>', open_selected)
        tree.bind('<Return>', lambda event: restore_selected() if backups else open_selected())
        refresh()
        self._finish_dialog(window)
        self.file_browser_window, self.file_browser_tree = window, tree
        return window

    def _open_folder(self, folder):
        if os.path.isdir(folder):
            try:
                os.startfile(os.path.abspath(folder))
            except OSError as exc:
                messagebox.showerror('无法打开文件夹', str(exc), parent=self.root)

    def _character_name(self, index):
        try:
            return self.db['types'][0]['rows'][index]['fields']['0']['value'] or '角色 %d' % (index + 1)
        except (KeyError, IndexError, TypeError):
            return '角色 %d' % (index + 1)

    def _change_description(self, key):
        table, row, field = key
        if key == self.core.T_POLLUTION:
            return '角色', '主角', '污染值'
        if table == 'elem':
            return '资源', '队伍', '元素 Elem'
        if key == self.core.T_MONEY:
            return '资源', '队伍', '金币 Chip'
        if table == 0:
            label = next((label for _, fid, label in self.core.CHAR_FIELDS if fid == field), None)
            label = label or next((name for fid, name in self.core.EQUIP_SLOTS if fid == field), '角色属性')
            return '装备' if field in self.equip_vars else '角色', self._character_name(row), label
        if table in (7, 8, 9):
            iid = {7: 10000, 8: 20000, 9: 30000}[table] + row
            return {7: '道具', 8: '武器', 9: '防具'}[table], self.names.get(iid, str(iid)), '数量'
        if table == 1:
            return '技能', self._character_name(row), '技能格 %02d · %s' % (field // 2 + 1, '编号' if field % 2 else '习得等级')
        return '存档', '记录 %d' % row, '数值'

    def change_review_rows(self):
        result = []
        for key, value in self.drafts.items():
            try:
                self._draft_number(key, value)
                valid = True
            except ValueError:
                valid = False
            result.append({'key': key, 'labels': self._change_description(key), 'old': self._value(key), 'new': value, 'valid': valid})
        return result

    def show_change_review(self):
        if self.db is None:
            self.say('请先载入一份存档')
            return
        if getattr(self, 'review_window', None) and self.review_window.winfo_exists():
            self.review_window.lift()
            self._refresh_review()
            return self.review_window
        window = self.review_window = self._dialog('修改预览 · 生成前核对')
        outer = ttk.Frame(window, padding=20)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='核对修改前后的数值', style='CardTitle.TLabel').pack(anchor='w')
        self.review_status = tk.StringVar()
        ttk.Label(outer, textvariable=self.review_status, style='Muted.TLabel', wraplength=850).pack(anchor='w', pady=(7, 14))
        self.review_tree = self._dialog_table(outer, [('category', '分类'), ('target', '角色 / 物品'), ('field', '项目'), ('old', '原值'), ('new', '改为')],
                                              [80, 245, 230, 120, 120])
        bar = ttk.Frame(outer)
        bar.pack(side='bottom', fill='x', pady=(14, 0), before=self.review_tree.master)
        def undo_selected():
            for item in self.review_tree.selection():
                self.drafts.pop(self.review_keys.get(item), None)
            self._sync_draft_controls()
            self._refresh_review()
        ttk.Button(bar, text='撤销选中', command=undo_selected).pack(side='left')
        ttk.Button(bar, text='撤销全部', command=lambda: (self.discard_all_changes(), self._refresh_review())).pack(side='left', padx=9)
        ttk.Button(bar, text='返回编辑', command=window.destroy).pack(side='right')
        def generate():
            try:
                if not self.collect_changes():
                    return
            except ValueError as exc:
                messagebox.showerror('请检查输入', str(exc), parent=window)
                return
            window.destroy()
            if self.current_page == 5:
                self.show_page(0)
            self.write_save()
        self.review_generate_button = ttk.Button(bar, text='生成新存档', style='Primary.TButton', command=generate)
        self.review_generate_button.pack(side='right', padx=9)
        self._refresh_review()
        self._finish_dialog(window)
        return window

    def _refresh_review(self):
        if not getattr(self, 'review_window', None) or not self.review_window.winfo_exists():
            return
        self.review_tree.delete(*self.review_tree.get_children())
        self.review_keys = {}
        rows = self.change_review_rows()
        for index, row in enumerate(rows):
            item = str(index)
            self.review_keys[item] = row['key']
            self.review_tree.insert('', 'end', iid=item, values=(*row['labels'], row['old'], row['new']),
                                    tags=() if row['valid'] else ('invalid',))
        invalid = sum(not row['valid'] for row in rows)
        self.review_status.set('%s · 共 %d 项%s' % (os.path.basename(self.path), len(rows),
                               '，其中 %d 项输入不完整，请返回编辑。' % invalid if invalid else '，可多选后撤销。'))
        self.review_generate_button.configure(state='normal' if rows and not invalid else 'disabled')

    def _sync_draft_controls(self):
        if self.db is None:
            return
        with self.updating():
            for name, key in [('chip', self.core.T_MONEY), ('elem', ('elem', 0, self.core.ELEM_V))]:
                value = self.drafts.get(key, self._value(key))
                self.resource_vars[name].set('' if value is None else str(value))
        self.load_character()
        self.load_equipment()
        self.load_items()
        self.load_skills()
        self._refresh_dirty()

    def discard_all_changes(self):
        if not self.drafts:
            self.say('当前没有待撤销的修改')
            return False
        if not messagebox.askyesno('撤销全部修改', '撤销当前 %d 项未生成的修改？' % len(self.drafts), parent=self.root):
            return False
        self.drafts.clear()
        self._sync_draft_controls()
        self.say('已恢复为载入存档时的数值')
        return True

    def _add_character_presets(self, page):
        outer, body = self.card(page, '角色能力方案', '填写已支持的基础属性，检查修改列表后再生成新存档。')
        outer.pack(fill='x', pady=(14, 0))
        line = ttk.Frame(body)
        line.pack(fill='x')
        self.character_preset = tk.StringVar(value='轻松探索')
        ttk.Combobox(line, textvariable=self.character_preset, values=list(CHARACTER_PRESETS), state='readonly', width=12).pack(side='left')
        self.character_preset_scope = tk.StringVar(value='当前角色')
        ttk.Combobox(line, textvariable=self.character_preset_scope, values=['当前角色', '列表中的全部角色'], state='readonly', width=18).pack(side='left', padx=9)
        ttk.Button(line, text='加入修改列表', style='Primary.TButton', command=self.apply_character_preset).pack(side='left')
        self.preset_keep_higher = tk.BooleanVar(value=True)
        ttk.Checkbutton(body, text='保留已高于方案的数值', variable=self.preset_keep_higher).pack(anchor='w', pady=(10, 0))

    def queue_character_preset(self, preset, characters, keep_higher=True):
        if self.db is None:
            raise ValueError('请先载入存档。')
        if preset not in CHARACTER_PRESETS:
            raise ValueError('请选择已有的角色方案。')
        queued = {}
        available = len(self.char_box['values'])
        for index in dict.fromkeys(characters):
            if not isinstance(index, int) or not 0 <= index < available:
                raise ValueError('角色不存在。')
            for field, desired in CHARACTER_PRESETS[preset].items():
                key = (0, index, field)
                current = self._integer(self.drafts.get(key, self._value(key)), self._character_name(index) + '的数值')
                queued[key] = max(current, desired) if keep_higher else desired
        for key, value in queued.items():
            self._set_draft(key, value)
        self._sync_draft_controls()
        return sum(self._value(key) != value for key, value in queued.items())

    def apply_character_preset(self):
        try:
            scope = range(len(self.char_box['values'])) if self.character_preset_scope.get() == '列表中的全部角色' else [self.char_index]
            count = self.queue_character_preset(self.character_preset.get(), scope, self.preset_keep_higher.get())
            self.say('角色方案已加入修改列表 · %d 项' % count)
        except ValueError as exc:
            messagebox.showerror('无法填写方案', str(exc), parent=self.root)

    def _add_inventory_batch(self, parent):
        ttk.Button(parent, text='批量调整已有物品', command=self.show_inventory_batch, padding=(9, 7)).pack(side='left', padx=(0, 6))

    def inventory_batch_keys(self, selected_only=True):
        items = self.item_tree.selection() if selected_only else self.item_tree.get_children()
        keys = []
        for item in items:
            key = self.item_keys.get(item)
            if key and (self._value(key) or 0) > 0:
                keys.append(key)
        return keys

    def queue_inventory_batch(self, keys, quantity):
        quantity = self._integer(quantity, '批量数量')
        if quantity > 9999:
            raise ValueError('批量数量请填写 0～9999。')
        keys = list(dict.fromkeys(keys))
        if not keys:
            raise ValueError('当前范围没有已持有物品。')
        for key in keys:
            if len(key) != 3 or key[0] not in (7, 8, 9) or key[2] != 0 or (self._value(key) or 0) <= 0:
                raise ValueError('批量操作只接受已持有物品的数量。')
        for key in keys:
            self._set_draft(key, quantity)
        self.load_items()
        self._refresh_dirty()
        return len(keys)

    def show_inventory_batch(self):
        if self.db is None:
            self.say('请先载入存档')
            return
        window = self._dialog('批量调整已有物品', 880, 560)
        outer = ttk.Frame(window, padding=20)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='批量填写物品数量', style='CardTitle.TLabel').pack(anchor='w')
        ttk.Label(outer, text='只处理原存档里已持有的物品；加入修改列表后仍可预览和撤销。', style='Muted.TLabel').pack(anchor='w', pady=(6, 14))
        bar = ttk.Frame(outer)
        bar.pack(fill='x', pady=(0, 14))
        selected = self.inventory_batch_keys(True)
        scope = tk.StringVar(value='选中的物品' if selected else '当前筛选列表')
        box = ttk.Combobox(bar, textvariable=scope, values=['选中的物品', '当前筛选列表'], state='readonly', width=17)
        box.pack(side='left')
        ttk.Label(bar, text='统一数量').pack(side='left', padx=(18, 9))
        quantity = tk.StringVar(value='99')
        ttk.Entry(bar, textvariable=quantity, width=9).pack(side='left')
        ttk.Label(bar, text='0～9999', style='Muted.TLabel').pack(side='left', padx=9)
        tree = self._dialog_table(outer, [('name', '将要调整的物品'), ('old', '当前数量')], [540, 130])
        planned = []
        count = tk.StringVar()
        def refresh():
            planned[:] = selected if scope.get() == '选中的物品' else self.inventory_batch_keys(False)
            tree.delete(*tree.get_children())
            for index, key in enumerate(planned):
                tree.insert('', 'end', iid=str(index), values=(self._change_description(key)[1], self._value(key)))
            count.set('本次范围：%s · %d 件' % (self.item_category.get(), len(planned)))
        def apply():
            try:
                number = self.queue_inventory_batch(planned, quantity.get())
            except ValueError as exc:
                messagebox.showerror('无法批量修改', str(exc), parent=window)
                return
            window.destroy()
            self.say('%d 件物品已加入修改列表' % number)
            self.show_change_review()
        footer = ttk.Frame(outer)
        footer.pack(side='bottom', fill='x', pady=(14, 0), before=tree.master)
        ttk.Label(footer, textvariable=count, style='Muted.TLabel').pack(side='left')
        ttk.Button(footer, text='加入修改列表', style='Primary.TButton', command=apply).pack(side='right')
        box.bind('<<ComboboxSelected>>', lambda event: refresh())
        refresh()
        self._finish_dialog(window)
        self.batch_window = window
        return window
