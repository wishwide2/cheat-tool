"""Native Tk desktop UI for the existing Akasan save and game-data backend."""
import os
import queue
import re
import sys
import threading
import tkinter as tk
from contextlib import contextmanager
from tkinter import filedialog, messagebox, simpledialog, ttk
from ui_workshop import WorkshopFeatures
from ui_launch import GameLaunchFeatures

BG = '#F3F6FA'
CARD = '#FFFFFF'
INK = '#18283C'
MUTED = '#78869A'
LINE = '#E2E8F0'
ACCENT = '#168775'
ACCENT_DARK = '#106B5D'
TINT = '#EAF7F2'
NAV = '#142638'
NAV_TEXT = '#A9B8C8'
FONT = 'Microsoft YaHei UI'

PAGES = [
    ('资源数值', '调整随身金币与元素，让下一段冒险更从容。', '◈'),
    ('角色成长', '调整主角污染值、角色等级和基础能力。', '◉'),
    ('装备配置', '查看每个角色的装备，并从已有库存中选择替换。', '◇'),
    ('道具库存', '按名称查找物品，把需要的数量加入修改列表。', '▤'),
    ('技能管理', '管理角色的技能记录与习得等级。', '✦'),
    ('游戏数据', '组合游戏规则与难度配置，应用后重新启动游戏。', '⚙'),
    ('启动游戏', '选择中文、日文或英文，继续你的冒险。', '▶'),
]


class EditorApp(GameLaunchFeatures, WorkshopFeatures):
    def __init__(self, core, root=None):
        self.core = core
        self.mc = core.MC
        self._load_workshop_preferences()
        self.root = root or tk.Tk()
        self.root.title('阿卡桑 · 冒险工坊  |  开源版 · 9.51')
        self.root.configure(bg=BG)
        icon_path = os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(__file__)), 'ui_icon.png')
        if os.path.isfile(icon_path):
            self.window_icon = tk.PhotoImage(file=icon_path)
            self.root.iconphoto(True, self.window_icon)
        width = min(1240, self.root.winfo_screenwidth() - 72)
        height = min(860, self.root.winfo_screenheight() - 100)
        self.root.geometry(f'{width}x{height}')
        self.root.minsize(1000, 680)
        self.loading = 0
        self.db = None
        self.bank0 = None
        self.path = None
        self.names = {}
        self.drafts = {}
        self.char_index = self.equip_index = self.skill_index = 0
        self.current_page = 0
        self.busy = False
        self.events = queue.Queue()
        self.equip_options = {}
        self.item_keys = {}
        self.skill_keys = {}
        self.status_var = tk.StringVar(value='准备就绪，选择一个存档开始编辑')
        self.pending_var = tk.StringVar(value='暂无待修改')
        self.file_name_var = tk.StringVar(value='尚未载入存档')
        self.file_path_var = tk.StringVar(value='打开 Save 文件夹中的 SaveData*.sav')
        self.page_title = tk.StringVar(value=PAGES[0][0])
        self.page_desc = tk.StringVar(value=PAGES[0][1])
        self._setup_style()
        self._build_shell()
        self._build_resources()
        self._build_characters()
        self._build_equipment()
        self._build_inventory()
        self._build_skills()
        self._build_mods()
        self._build_launch_page()
        self._build_footer()
        self._hydrate_mod_state()
        self._detect_game_directory()
        self._refresh_game_language(choose=True)
        self.show_page(6)
        self._refresh_dirty()
        self.apply_font_size(persist=False)
        self.root.bind('<Control-o>', lambda e: self.choose_save())
        self.root.bind('<Control-s>', lambda e: self.write_save())
        self.root.bind('<MouseWheel>', self._page_wheel, add='+')
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(120, self._drain_events)

    def _setup_style(self):
        style = self.style = ttk.Style(self.root)
        style.theme_use('clam')
        self.root.option_add('*TCombobox*Listbox.font', (FONT, 10))
        self.root.option_add('*TCombobox*Listbox.background', CARD)
        self.root.option_add('*TCombobox*Listbox.foreground', INK)
        self.root.option_add('*TCombobox*Listbox.selectBackground', ACCENT)
        self.root.option_add('*TCombobox*Listbox.selectForeground', CARD)
        style.configure('.', font=(FONT, 10), background=CARD, foreground=INK)
        style.configure('TFrame', background=CARD)
        style.configure('Page.TFrame', background=BG)
        style.configure('Card.TFrame', background=CARD, borderwidth=1, relief='solid',
                        bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
        style.configure('TLabel', background=CARD, foreground=INK)
        style.configure('Muted.TLabel', foreground=MUTED, font=(FONT, 9))
        style.configure('PageMuted.TLabel', background=BG, foreground=MUTED, font=(FONT, 9))
        style.configure('PageTitle.TLabel', background=BG, foreground=INK, font=(FONT, 22, 'bold'))
        style.configure('CardTitle.TLabel', font=(FONT, 11, 'bold'), foreground=INK)
        style.configure('Strong.TLabel', font=(FONT, 12, 'bold'))
        style.configure('Accent.TLabel', foreground=ACCENT, font=(FONT, 10, 'bold'))
        style.configure('Badge.TLabel', background=TINT, foreground=ACCENT_DARK, padding=(10, 4), font=(FONT, 9))
        style.configure('TEntry', fieldbackground='#F8FAFC', foreground=INK, padding=(10, 8),
                        bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, borderwidth=1)
        style.map('TEntry', bordercolor=[('focus', ACCENT)], fieldbackground=[('disabled', '#F2F4F7')])
        style.configure('TCombobox', padding=(9, 7), fieldbackground='#F8FAFC', background='#F8FAFC',
                        foreground=INK, arrowcolor=MUTED, bordercolor=LINE, lightcolor=LINE,
                        darkcolor=LINE, arrowsize=12)
        style.map('TCombobox', fieldbackground=[('readonly', '#F8FAFC')],
                  selectbackground=[('readonly', '#F8FAFC')], selectforeground=[('readonly', INK)],
                  bordercolor=[('focus', ACCENT)])
        style.configure('TButton', padding=(13, 9), background='#EDF1F6', foreground=INK,
                        borderwidth=0, focusthickness=0, font=(FONT, 10))
        style.map('TButton', background=[('active', '#E2E8F0'), ('disabled', '#F0F3F7')],
                  foreground=[('disabled', '#ACB6C4')])
        style.configure('Primary.TButton', background=ACCENT, foreground=CARD, font=(FONT, 10, 'bold'))
        style.map('Primary.TButton', background=[('active', ACCENT_DARK), ('disabled', '#B8D4CB')],
                  foreground=[('disabled', '#F9FCFB')])
        style.configure('Soft.TButton', background=TINT, foreground=ACCENT_DARK)
        style.map('Soft.TButton', background=[('active', '#D9F0E6')])
        style.configure('Danger.TButton', background='#FFF0ED', foreground='#AD5846')
        style.map('Danger.TButton', background=[('active', '#FAE1DC')])
        style.configure('Nav.TButton', background=NAV, foreground=NAV_TEXT, anchor='w',
                        padding=(17, 13), font=(FONT, 11), borderwidth=0)
        style.map('Nav.TButton', background=[('active', '#20384C')], foreground=[('active', CARD)])
        style.configure('ActiveNav.TButton', background='#24473F', foreground='#96E2CA', anchor='w',
                        padding=(17, 13), font=(FONT, 11, 'bold'), borderwidth=0)
        style.map('ActiveNav.TButton', background=[('active', '#295347')], foreground=[('active', CARD)])
        assets = os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(__file__)), 'ui_assets')
        self.check_images = {name: tk.PhotoImage(file=os.path.join(assets, 'check_' + name + '.png'))
                             for name in ('off', 'hover', 'on', 'on_hover', 'disabled_off', 'disabled_on')}
        style.element_create('Akasan.Check.indicator', 'image', self.check_images['off'],
                             ('disabled', 'selected', self.check_images['disabled_on']),
                             ('disabled', self.check_images['disabled_off']),
                             ('active', 'selected', self.check_images['on_hover']),
                             ('selected', self.check_images['on']),
                             ('active', self.check_images['hover']), sticky='w')
        style.layout('TCheckbutton', [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
            ('Akasan.Check.indicator', {'side': 'left', 'sticky': 'w'}),
            ('Checkbutton.focus', {'side': 'left', 'sticky': 'nswe', 'children': [
                ('Checkbutton.label', {'sticky': 'nswe'})]})]})])
        style.configure('TCheckbutton', background=CARD, foreground=INK, padding=(0, 4))
        style.map('TCheckbutton', background=[('active', CARD)],
                  foreground=[('disabled', MUTED)])
        style.configure('Treeview', background=CARD, fieldbackground=CARD, foreground=INK,
                        borderwidth=0, rowheight=35, font=(FONT, 10))
        style.configure('Treeview.Heading', background='#F1F5F9', foreground=MUTED,
                        relief='flat', font=(FONT, 9, 'bold'), padding=(12, 10))
        style.map('Treeview', background=[('selected', '#DDF3E9')],
                  foreground=[('selected', '#075D49')])
        style.map('Treeview.Heading', background=[('active', '#E8EEF5')])
        style.configure('Vertical.TScrollbar', background='#D5DEE7', troughcolor=BG,
                        borderwidth=0, arrowsize=10, gripcount=0)
        style.configure('TProgressbar', background=ACCENT, troughcolor='#E6EEF0', borderwidth=0)
        style.configure('Content.TNotebook', background=BG, borderwidth=0, padding=0,
                        bordercolor=BG, lightcolor=BG, darkcolor=BG)
        style.layout('Content.TNotebook.Tab', [])

    @contextmanager
    def updating(self):
        self.loading += 1
        try:
            yield
        finally:
            self.loading -= 1

    def card(self, parent, title='', subtitle='', padding=18):
        outer = ttk.Frame(parent, style='Card.TFrame', padding=padding)
        if title:
            ttk.Label(outer, text=title, style='CardTitle.TLabel').pack(anchor='w')
        if subtitle:
            ttk.Label(outer, text=subtitle, style='Muted.TLabel', wraplength=760).pack(anchor='w', pady=(5, 0))
        body = ttk.Frame(outer)
        body.pack(fill='both', expand=True, pady=(14 if title else 0, 0))
        return outer, body

    def _build_shell(self):
        sidebar = tk.Frame(self.root, bg=NAV, width=202)
        sidebar.pack(side='left', fill='y')
        sidebar.pack_propagate(False)
        brand = tk.Frame(sidebar, bg=NAV)
        brand.pack(fill='x', padx=23, pady=(30, 32))
        self.brand_icon = self.window_icon.subsample(3, 3)
        tk.Label(brand, image=self.brand_icon, bg=NAV, bd=0).pack(anchor='w', pady=(0, 13))
        tk.Label(brand, text='AKASAN', font=('Segoe UI', 18, 'bold'), bg=NAV, fg='#F7FAFD').pack(anchor='w')
        tk.Label(brand, text='冒险工坊', font=(FONT, 11), bg=NAV, fg='#9CB3C7').pack(anchor='w', pady=(3, 0))
        tk.Label(sidebar, text='工 作 空 间', bg=NAV, fg='#627D94', font=(FONT, 8)).pack(anchor='w', padx=24, pady=(0, 12))
        self.nav_buttons = {}
        for i in (6, 0, 1, 2, 3, 4, 5):
            title, _, icon = PAGES[i]
            button = ttk.Button(sidebar, text=f'{icon}   {title}', style='Nav.TButton',
                                command=lambda index=i: self.show_page(index), takefocus=True)
            button.pack(fill='x', padx=12, pady=3)
            self.nav_buttons[i] = button
        note = tk.Frame(sidebar, bg=NAV)
        note.pack(side='bottom', fill='x', padx=24, pady=24)
        tk.Label(note, text='●  本地工作空间', bg=NAV, fg='#76C7AC', font=(FONT, 9)).pack(anchor='w')
        tk.Label(note, text='适用于游戏 9.51', bg=NAV, fg='#71899F', font=(FONT, 9)).pack(anchor='w', pady=(7, 0))
        self.main = ttk.Frame(self.root, style='Page.TFrame')
        self.main.pack(side='left', fill='both', expand=True)
        toolbar = ttk.Frame(self.main, padding=(24, 17))
        toolbar.pack(fill='x')
        toolbar.columnconfigure(0, weight=1)
        file_block = ttk.Frame(toolbar)
        file_block.grid(row=0, column=0, sticky='ew')
        ttk.Label(file_block, textvariable=self.file_name_var, style='Strong.TLabel').pack(anchor='w')
        self.path_label = ttk.Label(file_block, textvariable=self.file_path_var, style='Muted.TLabel', wraplength=540)
        self.path_label.pack(anchor='w', fill='x', pady=(4, 0))
        file_block.bind('<Configure>', lambda event: self.path_label.configure(wraplength=max(100, event.width)))
        ttk.Button(toolbar, text='打开存档  ↗', style='Primary.TButton', command=self.choose_save).grid(row=0, column=1, padx=(16, 0))
        ttk.Separator(self.main).pack(fill='x')
        self.header = ttk.Frame(self.main, style='Page.TFrame', padding=(24, 20, 24, 17))
        self.header.pack(fill='x')
        ttk.Label(self.header, textvariable=self.page_title, style='PageTitle.TLabel').pack(anchor='w')
        ttk.Label(self.header, textvariable=self.page_desc, style='PageMuted.TLabel').pack(anchor='w', pady=(7, 0))
        self._build_quick_tools()
        self.notebook = ttk.Notebook(self.main, style='Content.TNotebook', takefocus=False)
        self.notebook.pack(fill='both', expand=True, padx=24, pady=(0, 14))
        self.pages = []
        self.page_canvases = {}
        for index, (title, _, _) in enumerate(PAGES):
            viewport = ttk.Frame(self.notebook, style='Page.TFrame')
            self.notebook.add(viewport, text=title)
            if index != 5:
                canvas = tk.Canvas(viewport, bg=BG, highlightthickness=0, bd=0)
                canvas.pack(side='left', fill='both', expand=True)
                scroll = ttk.Scrollbar(viewport, orient='vertical', command=canvas.yview)
                scroll.pack(side='right', fill='y')
                canvas.configure(yscrollcommand=scroll.set)
                page = ttk.Frame(canvas, style='Page.TFrame')
                window = canvas.create_window((0, 0), window=page, anchor='nw')
                def fit_page(event=None, cv=canvas, item=window, frame=page):
                    cv.itemconfigure(item, width=cv.winfo_width(),
                                     height=max(cv.winfo_height(), frame.winfo_reqheight()))
                    cv.configure(scrollregion=cv.bbox('all'))
                canvas.bind('<Configure>', fit_page)
                page.bind('<Configure>', fit_page)
                self.page_canvases[index] = canvas
            else:
                page = viewport
            self.pages.append(page)

    def _build_resources(self):
        page = self.pages[0]
        row = ttk.Frame(page, style='Page.TFrame')
        row.pack(fill='x')
        row.columnconfigure((0, 1), weight=1, uniform='resource')
        self.resource_vars = {}
        self.resource_original = {}
        for col, (key, title, unit, desc) in enumerate([
            ('chip', '随身金币', 'CHIP', '购买道具与装备的通用货币'),
            ('elem', '元素储备', 'ELEM', '队伍菜单中显示的元素数量'),
        ]):
            outer, body = self.card(row, title, desc, 22)
            outer.grid(row=0, column=col, sticky='nsew', padx=(0, 8) if col == 0 else (8, 0))
            ttk.Label(body, text=unit, style='Badge.TLabel').pack(anchor='w', pady=(0, 12))
            value = tk.StringVar(value='')
            entry = ttk.Entry(body, textvariable=value, font=('Segoe UI', 24, 'bold'), width=12)
            entry.pack(fill='x', pady=(0, 12))
            original = tk.StringVar(value='载入存档后显示当前数值')
            ttk.Label(body, textvariable=original, style='Muted.TLabel').pack(anchor='w')
            self.resource_vars[key] = value
            self.resource_original[key] = original
            value.trace_add('write', lambda *_, which=key: self._resource_changed(which))
        outer, body = self.card(page, '从一份存档开始', '编辑后的数值会生成到新的存档文件中。', 22)
        outer.pack(fill='x', pady=(18, 0))
        for col, (number, title, text) in enumerate([
            ('01', '选择存档', '打开游戏 Save 文件夹里的存档'),
            ('02', '调整数值', '切换左侧页面，逐项编辑'),
            ('03', '生成新档', '在游戏中重新读档查看效果'),
        ]):
            item = ttk.Frame(body)
            item.grid(row=0, column=col, sticky='nw', padx=(0, 18))
            body.columnconfigure(col, weight=1, uniform='steps')
            ttk.Label(item, text=number, foreground=ACCENT, font=('Segoe UI', 16, 'bold')).pack(anchor='w')
            ttk.Label(item, text=title, style='CardTitle.TLabel').pack(anchor='w', pady=(6, 5))
            ttk.Label(item, text=text, style='Muted.TLabel', wraplength=210).pack(anchor='w')

    def _character_picker(self, page, callback):
        bar = ttk.Frame(page, style='Page.TFrame')
        bar.pack(fill='x', pady=(0, 13))
        ttk.Label(bar, text='当前角色', style='PageMuted.TLabel').pack(side='left', padx=(0, 10))
        box = ttk.Combobox(bar, state='readonly', width=24)
        box.pack(side='left')
        box.bind('<<ComboboxSelected>>', callback)
        return box

    def _build_characters(self):
        page = self.pages[1]
        outer, body = self.card(page, '主角污染值', f'可输入 0 到 {self.core.POLLUTION_MAX} 的整数')
        outer.pack(fill='x', pady=(0, 14))
        self.pollution_var = tk.StringVar()
        self.pollution_original = tk.StringVar(value='载入存档后显示当前数值')
        self.pollution_entry = ttk.Entry(body, textvariable=self.pollution_var,
                                        width=9, font=('Segoe UI', 12), state='disabled')
        self.pollution_entry.pack(side='left')
        ttk.Label(body, textvariable=self.pollution_original, style='Muted.TLabel').pack(side='left', padx=(14, 0))
        self.pollution_var.trace_add('write', lambda *_: self._set_draft(self.core.T_POLLUTION, self.pollution_var.get()))
        self.char_box = self._character_picker(page, self._char_selected)
        self.char_vars = {}
        for title, keys, columns in [
            ('生命与等级', {'level', 'hp_max', 'sp_max', 'tp_max'}, 2),
            ('基础能力', {'atk', 'def', 'int', 'mdef', 'spd', 'hit'}, 3),
        ]:
            outer, body = self.card(page, title)
            outer.pack(fill='x', pady=(0, 14))
            for col in range(columns):
                body.columnconfigure(col, weight=1, uniform=title)
            fields = [f for f in self.core.CHAR_FIELDS if f[0] in keys]
            for i, (key, field, label) in enumerate(fields):
                form = ttk.Frame(body)
                form.grid(row=i // columns, column=i % columns, sticky='ew',
                          padx=(0, 14) if i % columns < columns - 1 else 0, pady=(0, 12))
                ttk.Label(form, text=label, style='Muted.TLabel').pack(anchor='w', pady=(0, 6))
                var = tk.StringVar()
                ttk.Entry(form, textvariable=var, width=13, font=('Segoe UI', 12)).pack(fill='x')
                self.char_vars[field] = var
                var.trace_add('write', lambda *_, fi=field: self._character_value_changed(fi))
        ttk.Label(page, text='这里编辑基础能力；游戏中显示的数值还会计入装备加成。', style='PageMuted.TLabel').pack(anchor='w')
        self._add_character_presets(page)

    def _build_equipment(self):
        page = self.pages[2]
        self.equip_character_box = self._character_picker(page, self._equip_selected)
        outer, body = self.card(page, '装备方案', '可以选择库存中的装备，也可以直接输入装备编号。')
        outer.pack(fill='both', expand=True)
        body.columnconfigure((0, 1), weight=1, uniform='equip')
        self.equip_vars = {}
        self.equip_boxes = {}
        for i, (field, name) in enumerate(self.core.EQUIP_SLOTS):
            cell = ttk.Frame(body)
            cell.grid(row=i // 2, column=i % 2, sticky='ew', padx=(0, 16) if i % 2 == 0 else 0, pady=(0, 12))
            ttk.Label(cell, text='扩展装备' if field == 68 else name, style='Muted.TLabel').pack(anchor='w', pady=(0, 5))
            var = tk.StringVar()
            cb = ttk.Combobox(cell, textvariable=var, width=25)
            cb.pack(fill='x')
            self.equip_vars[field] = var
            self.equip_boxes[field] = cb
            var.trace_add('write', lambda *_, fi=field: self._equipment_changed(fi))

    def _tree(self, parent, columns, widths):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show='headings', selectmode='browse', height=9)
        for (key, title), width in zip(columns, widths):
            tree.heading(key, text=title, anchor='w')
            tree.column(key, width=width, minwidth=65, stretch=key == columns[0][0], anchor='w')
        tree.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        scroll.pack(side='right', fill='y')
        tree.configure(yscrollcommand=scroll.set)
        tree.tag_configure('odd', background='#F8FAFC')
        tree.tag_configure('pending', foreground=ACCENT_DARK)
        return tree

    def _build_inventory(self):
        page = self.pages[3]
        filters = ttk.Frame(page, style='Page.TFrame')
        filters.pack(fill='x', pady=(0, 13))
        self.item_category = tk.StringVar(value='道具')
        self.item_category_box = ttk.Combobox(filters, textvariable=self.item_category, values=['道具', '武器', '防具'], state='readonly', width=10)
        self.item_category_box.pack(side='left', padx=(0, 12))
        self.item_category_box.bind('<<ComboboxSelected>>', lambda e: self.load_items())
        self.item_search = tk.StringVar()
        search = ttk.Entry(filters, textvariable=self.item_search, width=26)
        search.pack(side='left', fill='x', expand=True)
        self.item_search.trace_add('write', lambda *_: self.load_items())
        ttk.Label(filters, text='搜索名称或编号', style='PageMuted.TLabel').pack(side='left', padx=12)
        batch_bar = ttk.Frame(page, style='Page.TFrame')
        batch_bar.pack(fill='x', pady=(0, 13))
        self._add_inventory_batch(batch_bar)
        ttk.Label(batch_bar, text='按 Ctrl 可多选 · 支持中文、原文与编号搜索', style='PageMuted.TLabel').pack(side='left', padx=10)
        split = ttk.Frame(page, style='Page.TFrame')
        split.pack(fill='both', expand=True)
        left, body = self.card(split, '持有物品', padding=16)
        left.pack(side='left', fill='both', expand=True, padx=(0, 14))
        self.item_count_var = tk.StringVar(value='尚未载入存档')
        ttk.Label(body, textvariable=self.item_count_var, style='Muted.TLabel').pack(anchor='w', pady=(0, 12))
        self.item_tree = self._tree(body, [('name', '名称'), ('quantity', '当前'), ('new', '修改后')], [280, 72, 82])
        self.item_tree.configure(selectmode='extended')
        self.item_tree.bind('<<TreeviewSelect>>', self._item_selected)
        self.item_empty = ttk.Label(self.item_tree, text='打开存档后，这里会显示你的库存', style='Muted.TLabel')
        self.item_empty.place(relx=.5, rely=.5, anchor='center')
        right, form = self.card(split, '数量调整', padding=18)
        right.pack(side='left', fill='y')
        right.configure(width=242)
        self.item_pick_name = tk.StringVar(value='先选择一件物品')
        self.item_pick_current = tk.StringVar(value='点击左侧列表中的条目')
        ttk.Label(form, textvariable=self.item_pick_name, style='CardTitle.TLabel', wraplength=195).pack(anchor='w', pady=(0, 8))
        ttk.Label(form, textvariable=self.item_pick_current, style='Muted.TLabel', wraplength=195).pack(anchor='w', pady=(0, 24))
        ttk.Label(form, text='修改后的数量', style='Muted.TLabel').pack(anchor='w', pady=(0, 7))
        self.item_quantity = tk.StringVar()
        ttk.Entry(form, textvariable=self.item_quantity, width=15, font=('Segoe UI', 16)).pack(fill='x', pady=(0, 15))
        ttk.Button(form, text='应用修改', style='Primary.TButton', command=self.apply_item).pack(fill='x')
        ttk.Button(form, text='撤销这一项', command=self.undo_item).pack(fill='x', pady=(8, 0))
        ttk.Label(form, text='先应用到修改列表，最后统一生成新存档。', style='Muted.TLabel', wraplength=195).pack(anchor='w', pady=(20, 0))

    def _build_skills(self):
        page = self.pages[4]
        self.skill_character_box = self._character_picker(page, self._skill_selected_character)
        split = ttk.Frame(page, style='Page.TFrame')
        split.pack(fill='both', expand=True)
        left, body = self.card(split, '角色技能', padding=16)
        left.pack(side='left', fill='both', expand=True, padx=(0, 14))
        self.skill_tree = self._tree(body, [('slot', '技能格'), ('skill', '技能编号'), ('level', '习得等级'), ('status', '状态')], [90, 130, 90, 80])
        self.skill_tree.bind('<<TreeviewSelect>>', self._skill_selected)
        self.skill_empty = ttk.Label(self.skill_tree, text='载入存档后选择角色，查看技能记录', style='Muted.TLabel')
        self.skill_empty.place(relx=.5, rely=.5, anchor='center')
        right, form = self.card(split, '技能调整', padding=18)
        right.pack(side='left', fill='y')
        self.skill_pick_name = tk.StringVar(value='先选择一个技能格')
        ttk.Label(form, textvariable=self.skill_pick_name, style='CardTitle.TLabel', wraplength=195).pack(anchor='w', pady=(0, 20))
        self.skill_id = tk.StringVar()
        self.skill_level = tk.StringVar()
        for title, var in [('技能编号', self.skill_id), ('习得等级', self.skill_level)]:
            ttk.Label(form, text=title, style='Muted.TLabel').pack(anchor='w', pady=(0, 6))
            ttk.Entry(form, textvariable=var, width=15, font=('Segoe UI', 13)).pack(fill='x', pady=(0, 15))
        ttk.Button(form, text='应用修改', style='Primary.TButton', command=self.apply_skill).pack(fill='x')
        ttk.Button(form, text='撤销这一项', command=self.undo_skill).pack(fill='x', pady=(8, 0))
        ttk.Label(form, text='技能编号填 -1 时清空该格；等级 0 表示起始习得。', style='Muted.TLabel', wraplength=195).pack(anchor='w', pady=(20, 0))

    def _build_mods(self):
        page = self.pages[5]
        self.mod_canvas = tk.Canvas(page, bg=BG, highlightthickness=0, bd=0)
        self.mod_canvas.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(page, orient='vertical', command=self.mod_canvas.yview)
        scroll.pack(side='right', fill='y')
        self.mod_canvas.configure(yscrollcommand=scroll.set)
        content = ttk.Frame(self.mod_canvas, style='Page.TFrame')
        window = self.mod_canvas.create_window((0, 0), window=content, anchor='nw')
        self.mod_canvas.bind('<Configure>', lambda e: self.mod_canvas.itemconfigure(window, width=e.width))
        content.bind('<Configure>', lambda e: self.mod_canvas.configure(scrollregion=self.mod_canvas.bbox('all')))
        outer, body = self.card(content, padding=16)
        outer.pack(fill='x', pady=(0, 12))
        ttk.Label(body, text='游戏目录', style='CardTitle.TLabel').pack(side='left', padx=(0, 12))
        self.game_path = tk.StringVar()
        ttk.Entry(body, textvariable=self.game_path).pack(side='left', fill='x', expand=True, padx=(0, 10))
        ttk.Button(body, text='选择目录', command=self.choose_game).pack(side='left')
        preset_bar = ttk.Frame(content, style='Page.TFrame')
        preset_bar.pack(fill='x', pady=(0, 12))
        ttk.Label(preset_bar, text='我的配置', style='PageMuted.TLabel').pack(side='left', padx=(0, 9))
        self.preset_name = tk.StringVar()
        self.preset_box = ttk.Combobox(preset_bar, textvariable=self.preset_name, state='readonly', width=18)
        self.preset_box.pack(side='left', padx=(0, 6))
        ttk.Button(preset_bar, text='读取', command=self.load_preset).pack(side='left')
        ttk.Button(preset_bar, text='保存为…', command=self.save_preset).pack(side='left', padx=6)
        ttk.Button(preset_bar, text='删除', command=self.delete_preset).pack(side='left')
        ttk.Button(preset_bar, text='爽玩预设', style='Soft.TButton', command=self.play_preset).pack(side='right')
        self.mod_vars = {key: tk.BooleanVar(value=False) for key in ('price', 'enemy', 'drop', 'equip', 'skill')}
        choices = ttk.Frame(content, style='Page.TFrame')
        choices.pack(fill='x', pady=(0, 12))
        choices.columnconfigure((0, 1), weight=1, uniform='mods')
        left, left_body = self.card(choices, '冒险便利', padding=18)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 7))
        for key, title, hint in [
            ('price', '商店友好价格', '道具、武器与防具的商店价格设为 1'),
            ('drop', '满额掉落与收益', '掉落率 100%，经验和金币收益 ×3'),
            ('skill', '技能零消耗', '技能的 SP / TP 消耗设为 0'),
        ]:
            ttk.Checkbutton(left_body, text=title, variable=self.mod_vars[key]).pack(anchor='w')
            ttk.Label(left_body, text=hint, style='Muted.TLabel', wraplength=350).pack(anchor='w', padx=(26, 0), pady=(0, 10))
        right, right_body = self.card(choices, '战斗平衡', padding=18)
        right.grid(row=0, column=1, sticky='nsew', padx=(7, 0))
        self.enemy_scale = tk.StringVar(value='0.5')
        self.equipment_scale = tk.StringVar(value='1.5')
        for key, title, hint, variable, table in [
            ('enemy', '敌人强度', '数值越低，敌人的基础能力越弱', self.enemy_scale, self.mc.SCALES if self.mc else []),
            ('equip', '我方装备增益', '提高装备提供的属性加成', self.equipment_scale, self.mc.EQUIP_SCALES if self.mc else []),
        ]:
            line = ttk.Frame(right_body)
            line.pack(fill='x', pady=(0, 5))
            ttk.Checkbutton(line, text=title, variable=self.mod_vars[key]).pack(side='left')
            ttk.Combobox(line, textvariable=variable, values=[n for n, _ in table], width=13).pack(side='right')
            ttk.Label(right_body, text=hint, style='Muted.TLabel', wraplength=330).pack(anchor='w', padx=(26, 0), pady=(0, 16))
        ttk.Label(right_body, text='支持自定义：0.37 / 2.8 / 50%', style='Muted.TLabel').pack(anchor='w')
        actions = ttk.Frame(content, style='Page.TFrame')
        actions.pack(fill='x', pady=(0, 13))
        self.mod_apply_button = ttk.Button(actions, text='应用到游戏', style='Primary.TButton', command=self.apply_mods)
        self.mod_apply_button.pack(side='left')
        self.mod_restore_button = ttk.Button(actions, text='恢复原版', command=self.restore_mods)
        self.mod_restore_button.pack(side='left', padx=8)
        ttk.Button(actions, text='检查状态', command=self.mod_status).pack(side='left')
        self.mod_progress = ttk.Progressbar(actions, length=85, mode='indeterminate')
        self.mod_summary = tk.StringVar(value='尚未选择改动')
        ttk.Label(actions, textvariable=self.mod_summary, style='PageMuted.TLabel').pack(side='right')
        console = tk.Frame(content, bg='#152638', padx=16, pady=13)
        console.pack(fill='both', expand=True)
        tk.Label(console, text='执行记录', bg='#152638', fg='#9BB5C8', font=(FONT, 9, 'bold')).pack(anchor='w', pady=(0, 8))
        self.mod_log = tk.Text(console, height=6, font=(FONT, 9), bg='#152638', fg='#D8E4EE',
                               relief='flat', bd=0, highlightthickness=0, wrap='word', state='disabled', insertbackground=CARD)
        self.mod_log.pack(fill='both', expand=True)
        for var in self.mod_vars.values():
            var.trace_add('write', lambda *_: self._update_mod_summary())
        self.enemy_scale.trace_add('write', lambda *_: self._update_mod_summary())
        self.equipment_scale.trace_add('write', lambda *_: self._update_mod_summary())
        self._mod_log('每次从原版基线生成；切换配置不会叠加修改。')
        self._mod_log('请先退出游戏，应用完成后重新启动。')

    def _build_footer(self):
        ttk.Separator(self.main).pack(fill='x')
        self.activity_frame = ttk.Frame(self.main, padding=(24, 10))
        self.activity = tk.Text(self.activity_frame, height=4, font=(FONT, 9), bg='#F7F9FC', fg=MUTED,
                                 bd=0, highlightthickness=0, wrap='word', state='disabled')
        self.activity.pack(fill='x')
        self.footer = ttk.Frame(self.main, padding=(24, 14))
        self.footer.pack(fill='x')
        self.write_button = ttk.Button(self.footer, text='生成新存档  →', style='Primary.TButton', command=self.write_save)
        self.write_button.pack(side='right')
        ttk.Label(self.footer, textvariable=self.pending_var, style='Badge.TLabel').pack(side='right', padx=(10, 14))
        ttk.Button(self.footer, text='操作记录', command=self.toggle_activity).pack(side='right', padx=(8, 0))
        self.status_label = ttk.Label(self.footer, textvariable=self.status_var, style='Muted.TLabel', wraplength=420)
        self.status_label.pack(side='left', fill='x', expand=True)

    def show_page(self, index):
        self.current_page = index
        self.notebook.select(index)
        self.page_title.set(PAGES[index][0])
        self.page_desc.set(PAGES[index][1])
        for i, button in self.nav_buttons.items():
            button.configure(style='ActiveNav.TButton' if i == index else 'Nav.TButton')
        if hasattr(self, 'write_button'):
            self._refresh_dirty()

    def _page_wheel(self, event):
        canvas = self.mod_canvas if self.current_page == 5 else self.page_canvases.get(self.current_page)
        if (canvas is not None and not isinstance(event.widget, (tk.Text, ttk.Combobox))
                and canvas.bbox('all') and canvas.bbox('all')[3] > canvas.winfo_height()):
            canvas.yview_scroll(-int(event.delta / 120), 'units')

    def _append(self, widget, text):
        widget.configure(state='normal')
        widget.insert('end', text + '\n')
        widget.see('end')
        widget.configure(state='disabled')

    def say(self, message):
        self.status_var.set(message)
        if hasattr(self, 'activity'):
            self._append(self.activity, message)

    def _mod_log(self, message):
        self._append(self.mod_log, message)

    def toggle_activity(self):
        if self.activity_frame.winfo_manager():
            self.activity_frame.pack_forget()
        else:
            self.activity_frame.pack(fill='x', before=self.footer)

    def _field(self, key):
        if self.db is None:
            return None
        table, row, field = key
        if table in ('elem', 'pollution'):
            values = self.bank0['values'] if self.bank0 else ()
            return values[field] if 0 <= field < len(values) else None
        found = self.db['types'][table]['rows'][row]['fields'].get(str(field))
        return found if found and found['kind'] == 'i32' else None

    def _value(self, key):
        field = self._field(key)
        return field['value'] if field is not None else None

    def _set_draft(self, key, value):
        if self.loading or self.db is None or self._value(key) is None:
            return
        try:
            unchanged = int(str(value).strip()) == self._value(key)
        except ValueError:
            unchanged = False
        if unchanged:
            self.drafts.pop(key, None)
        else:
            self.drafts[key] = value
        self._refresh_dirty()

    def _refresh_dirty(self):
        count = len(self.drafts)
        self.pending_var.set(f'{count} 项待修改' if count else '暂无待修改')
        if hasattr(self, 'review_button'):
            self.review_button.configure(state='normal' if self.db is not None else 'disabled')
            self.discard_button.configure(state='normal' if count else 'disabled')
        if hasattr(self, 'write_button'):
            self.write_button.configure(state='normal' if self.db is not None and self.current_page != 5 else 'disabled')

    def _resource_changed(self, which):
        key = self.core.T_MONEY if which == 'chip' else ('elem', 0, self.core.ELEM_V)
        self._set_draft(key, self.resource_vars[which].get())

    def _character_value_changed(self, field):
        self._set_draft((0, self.char_index, field), self.char_vars[field].get())

    def _equipment_changed(self, field):
        text = self.equip_vars[field].get().strip()
        value = self.equip_options.get(text, text)
        if str(value).strip() == '0':
            value = -1
        self._set_draft((0, self.equip_index, field), value)

    def choose_save(self):
        folders = self._save_folders()
        path = filedialog.askopenfilename(parent=self.root, title='选择游戏存档',
                                          initialdir=folders[0] if folders else None,
                                          filetypes=[('游戏存档', '*.sav'), ('全部文件', '*.*')])
        if path:
            self.open_save(path)

    def open_save(self, path, ask_discard=True):
        if ask_discard and self.drafts and not messagebox.askyesno('切换存档', '当前还有未生成的修改，是否放弃并打开另一份存档？', parent=self.root):
            return False
        try:
            raw, plain = self.core.load_save(path)
            db, bank0 = self.core.parse_all(plain)
        except Exception as exc:
            messagebox.showerror('无法打开存档', f'{exc}\n\n请使用游戏 9.51 的存档。', parent=self.root)
            return False
        self.path, self.raw, self.plain, self.db, self.bank0 = path, raw, plain, db, bank0
        self.drafts.clear()
        self.names = self.core.build_names(self.db)
        names = []
        for row in db['types'][0]['rows'][:8]:
            names.append(row['fields']['0']['value'] or f'角色 {row["row"] + 1}')
        with self.updating():
            for box in (self.char_box, self.equip_character_box, self.skill_character_box):
                box['values'] = names
                box.current(0)
            self.char_index = self.equip_index = self.skill_index = 0
            chip = self._value(self.core.T_MONEY)
            elem = self._value(('elem', 0, self.core.ELEM_V))
            self.resource_vars['chip'].set(str(chip) if chip is not None else '')
            self.resource_vars['elem'].set(str(elem) if elem is not None else '')
            self.resource_original['chip'].set(f'当前存档 · {chip:,} Chip' if chip is not None else '当前存档没有可读取的金币')
            self.resource_original['elem'].set(f'当前存档 · {elem:,} Elem' if elem is not None else '当前存档没有可读取的元素')
        self.file_name_var.set(os.path.basename(path))
        self.file_path_var.set(os.path.dirname(os.path.abspath(path)))
        self.load_character()
        self.load_equipment()
        self.load_items()
        self.load_skills()
        self._refresh_dirty()
        self.say(f'已载入 {os.path.basename(path)}，可以开始编辑')
        self._remember_save(path)
        return True

    def _char_selected(self, event=None):
        self.char_index = max(0, self.char_box.current())
        self.load_character()

    def load_character(self):
        if self.db is None:
            return
        with self.updating():
            key = self.core.T_POLLUTION
            original = self._value(key)
            value = self.drafts.get(key, original)
            self.pollution_var.set('' if value is None else str(value))
            self.pollution_entry.configure(state='disabled' if original is None else 'normal')
            self.pollution_original.set('此存档未读取到污染值' if original is None else f'当前存档 · {original}')
            for field, var in self.char_vars.items():
                key = (0, self.char_index, field)
                value = self.drafts.get(key, self._value(key))
                var.set('' if value is None else str(value))

    def _equip_selected(self, event=None):
        self.equip_index = max(0, self.equip_character_box.current())
        self.load_equipment()

    def load_equipment(self):
        if self.db is None:
            return
        options = {'不装备': -1}
        for table, base, category in ((8, 20000, '武器'), (9, 30000, '防具')):
            for row in self.db['types'][table]['rows']:
                count = row['fields']['0']['value']
                if count > 0:
                    iid = base + row['row']
                    label = f'{category} · {self.names.get(iid) or str(iid)}  /  库存 {count}'
                    options[label] = iid
        labels = {}
        for field, _ in self.core.EQUIP_SLOTS:
            key = (0, self.equip_index, field)
            value = self.drafts.get(key, self._value(key))
            try:
                value = int(value)
            except (ValueError, TypeError):
                labels[field] = str(value or '')
                continue
            if value == -1:
                label = '不装备'
            elif value < -1:
                label = f'未开放（{value}）'
                options[label] = value
            else:
                label = f'已装备 · {self.names.get(value) or str(value)}'
                options[label] = value
            labels[field] = label
        self.equip_options = options
        with self.updating():
            for field, box in self.equip_boxes.items():
                box['values'] = list(options)
                self.equip_vars[field].set(labels[field])

    def item_table(self):
        return {'道具': 7, '武器': 8, '防具': 9}[self.item_category.get()]

    def load_items(self):
        if not hasattr(self, 'item_tree'):
            return
        selected = self.item_tree.selection()
        keep = selected[0] if selected else None
        self.item_tree.delete(*self.item_tree.get_children())
        self.item_keys.clear()
        if self.db is None:
            return
        table = self.item_table()
        base = {7: 10000, 8: 20000, 9: 30000}[table]
        query = self.item_search.get().strip().casefold()
        for row in self.db['types'][table]['rows']:
            index, current = row['row'], row['fields']['0']['value']
            iid = base + index
            key = (table, index, 0)
            changed = self.drafts.get(key)
            if current == 0 and changed is None and not query:
                continue
            name = self.names.get(iid) or f'物品 {iid}'
            if query and query not in f'{name} {iid}'.casefold():
                continue
            item = f'{table}:{index}'
            tags = ('odd',) if len(self.item_keys) % 2 else ()
            if changed is not None:
                tags += ('pending',)
            self.item_tree.insert('', 'end', iid=item, values=(name, current, changed if changed is not None else '—'), tags=tags)
            self.item_keys[item] = key
        total = len(self.item_keys)
        self.item_count_var.set(f'共 {total} 项' + (' · 已筛选' if query else ' · 显示有数量的物品'))
        if total:
            self.item_empty.place_forget()
        else:
            self.item_empty.configure(text='没有找到匹配的物品，试试其他关键词')
            self.item_empty.place(relx=.5, rely=.5, anchor='center')
        if keep and keep in self.item_keys:
            self.item_tree.selection_set(keep)
            self._item_selected()
        else:
            self.item_pick_name.set('先选择一件物品')
            self.item_pick_current.set('点击左侧列表中的条目')
            self.item_quantity.set('')

    def _item_selected(self, event=None):
        selected = self.item_tree.selection()
        if not selected or selected[0] not in self.item_keys:
            return
        key = self.item_keys[selected[0]]
        self.item_pick_name.set(str(self.item_tree.item(selected[0], 'values')[0]))
        self.item_pick_current.set(f'当前数量  {self._value(key):,}')
        self.item_quantity.set(str(self.drafts.get(key, self._value(key))))

    def _integer(self, value, label, minimum=0, maximum=2147483647):
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            raise ValueError(f'{label}请输入整数') from None
        if not minimum <= number <= maximum:
            raise ValueError(f'{label}需在 {minimum} 到 {maximum} 之间')
        return number

    def apply_item(self):
        selected = self.item_tree.selection()
        if not selected:
            self.say('请先在列表中选择一件物品')
            return
        try:
            number = self._integer(self.item_quantity.get(), '物品数量')
        except ValueError as exc:
            messagebox.showerror('数量不正确', str(exc), parent=self.root)
            return
        self._set_draft(self.item_keys[selected[0]], number)
        self.load_items()
        self.say('物品数量已加入修改列表，生成新存档后生效')

    def undo_item(self):
        selected = self.item_tree.selection()
        if selected:
            self.drafts.pop(self.item_keys[selected[0]], None)
            self.load_items()
            self._refresh_dirty()
            self.say('已撤销这件物品的数量修改')

    def _skill_selected_character(self, event=None):
        self.skill_index = max(0, self.skill_character_box.current())
        self.load_skills()

    def load_skills(self):
        if self.db is None:
            return
        selected = self.skill_tree.selection()
        keep = selected[0] if selected else None
        self.skill_tree.delete(*self.skill_tree.get_children())
        self.skill_keys.clear()
        table = self.db['types'][1]
        if self.skill_index >= table['row_count']:
            return
        row = table['rows'][self.skill_index]['fields']
        for slot in range(50):
            if str(slot * 2 + 1) not in row:
                break
            level_key, id_key = (1, self.skill_index, slot * 2), (1, self.skill_index, slot * 2 + 1)
            lv, sid = self.drafts.get(level_key, self._value(level_key)), self.drafts.get(id_key, self._value(id_key))
            pending = level_key in self.drafts or id_key in self.drafts
            empty = lv == -1 and sid == -1
            item = f'{self.skill_index}:{slot}'
            tags = ('odd',) if slot % 2 else ()
            if pending:
                tags += ('pending',)
            self.skill_tree.insert('', 'end', iid=item, values=(f'{slot + 1:02d}', '—' if empty else sid,
                                   '—' if empty else lv, '待修改' if pending else '空位' if empty else '已记录'), tags=tags)
            self.skill_keys[item] = (level_key, id_key)
        self.skill_empty.place_forget()
        if keep in self.skill_keys:
            self.skill_tree.selection_set(keep)
            self._skill_selected()

    def _skill_selected(self, event=None):
        selected = self.skill_tree.selection()
        if not selected or selected[0] not in self.skill_keys:
            return
        level_key, id_key = self.skill_keys[selected[0]]
        self.skill_pick_name.set(f'技能格 {id_key[2] // 2 + 1:02d}')
        self.skill_id.set(str(self.drafts.get(id_key, self._value(id_key))))
        self.skill_level.set(str(self.drafts.get(level_key, self._value(level_key))))

    def apply_skill(self):
        selected = self.skill_tree.selection()
        if not selected:
            self.say('请先在列表中选择一个技能格')
            return
        try:
            sid = self._integer(self.skill_id.get(), '技能编号', -1)
            level = -1 if sid == -1 else self._integer(self.skill_level.get(), '习得等级', -1)
        except ValueError as exc:
            messagebox.showerror('技能数值不正确', str(exc), parent=self.root)
            return
        level_key, id_key = self.skill_keys[selected[0]]
        self._set_draft(level_key, level)
        self._set_draft(id_key, sid)
        self.load_skills()
        self.say('技能记录已加入修改列表')

    def undo_skill(self):
        selected = self.skill_tree.selection()
        if selected:
            for key in self.skill_keys[selected[0]]:
                self.drafts.pop(key, None)
            self.load_skills()
            self._refresh_dirty()
            self.say('已撤销这个技能格的修改')

    def _draft_number(self, key, value):
        if key == self.core.T_POLLUTION:
            return self._integer(value, '主角污染值', maximum=self.core.POLLUTION_MAX)
        equip = key[0] == 0 and key[2] in self.equip_vars
        skill = key[0] == 1
        return self._integer(value, '待修改的数值', -2147483648 if equip else -1 if skill else 0)

    def collect_changes(self):
        edits = []
        for key, value in self.drafts.items():
            number = self._draft_number(key, value)
            field = self._field(key)
            if field is None:
                raise ValueError('当前存档缺少待修改的数值，请重新打开存档')
            if number != field['value']:
                edits.append((field['offset'], field['value'], number))
        return edits

    def write_save(self):
        if self.db is None or self.current_page == 5:
            return
        try:
            edits = self.collect_changes()
        except ValueError as exc:
            messagebox.showerror('请检查输入', str(exc), parent=self.root)
            return
        if not edits:
            self.say('目前没有修改；道具或技能需要先点击“应用修改”')
            return
        folder = os.path.dirname(os.path.abspath(self.path))
        used = {int(m.group(1)) for name in os.listdir(folder)
                if (m := re.match(r'^SaveData0*(\d+)\.sav$', name, re.I))}
        slot = next((n for n in range(1, 501) if n not in used), 501)
        out = filedialog.asksaveasfilename(parent=self.root, title='生成到一个新的存档槽位',
                                           initialdir=folder, initialfile=f'SaveData{slot:02d}.sav',
                                           defaultextension='.sav', filetypes=[('游戏存档', '*.sav')])
        if not out:
            return
        out, _ = self.core.normalize_save_name(out)
        if os.path.exists(out) or os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(self.path)):
            messagebox.showerror('请选择新槽位', '该位置已有文件，请选择一个未使用的存档槽位。', parent=self.root)
            return
        try:
            self.core.backup(self.path)
            self.core.apply_edits(self.path, out, edits)
            self.open_save(out, ask_discard=False)
            self.say(f'已生成 {os.path.basename(out)} · {len(edits)} 项修改已通过校验')
            messagebox.showinfo('新存档已生成', '已自动备份原档，并生成通过校验的新存档。\n进游戏重新读档即可查看效果。', parent=self.root)
        except Exception as exc:
            self.say('生成失败：' + str(exc))
            messagebox.showerror('无法生成存档', str(exc), parent=self.root)

    def _hydrate_mod_state(self):
        if not self.mc:
            self._mod_log('游戏数据模块不可用；存档编辑功能仍可使用。')
            return
        try:
            state = self.mc.load_state()
            self.game_path.set(state.get('game', ''))
            for key, var in self.mod_vars.items():
                var.set(key in (state.get('mods') or []))
            self.enemy_scale.set('%g' % state.get('scale', .5))
            self.equipment_scale.set('%g' % state.get('equip_scale', 1.5))
            self.refresh_presets()
        except Exception as exc:
            self._mod_log('无法读取上次配置：' + str(exc))
        self._update_mod_summary()

    def _update_mod_summary(self):
        if not hasattr(self, 'mod_summary'):
            return
        count = sum(v.get() for v in self.mod_vars.values())
        self.mod_summary.set(f'已选择 {count} 项改动' if count else '未启用改动 · 应用将恢复原版')

    def refresh_presets(self, select=None):
        if not self.mc:
            return
        names = sorted(self.mc.load_presets())
        self.preset_box['values'] = names
        if select in names:
            self.preset_name.set(select)
        elif self.preset_name.get() not in names:
            self.preset_name.set(names[0] if names else '')

    def load_preset(self):
        if not self.mc:
            return
        config = self.mc.load_presets().get(self.preset_name.get())
        if config is None:
            self._mod_log('请先选择一套已保存的配置。')
            return
        for key, var in self.mod_vars.items():
            var.set(key in config.get('mods', []))
        self.enemy_scale.set('%g' % config.get('scale', 1.0))
        self.equipment_scale.set('%g' % config.get('equip_scale', 1.0))
        self._mod_log('已载入配置「%s」，点击“应用到游戏”后生效。' % self.preset_name.get())

    def save_preset(self):
        if not self.mc:
            return
        try:
            _, chosen, enemy, equip = self._mod_settings(require_game=False)
        except ValueError as exc:
            messagebox.showerror('请检查倍率', str(exc), parent=self.root)
            return
        name = simpledialog.askstring('保存配置', '给这套配置起个名字', initialvalue=self.preset_name.get() or '我的配置', parent=self.root)
        if name and name.strip():
            self.mc.save_preset(name.strip(), chosen, enemy, equip)
            self.refresh_presets(select=name.strip())
            self._mod_log(f'已保存配置「{name.strip()}」。')

    def delete_preset(self):
        name = self.preset_name.get()
        if self.mc and name and messagebox.askyesno('删除配置', f'删除配置「{name}」？\n不会改动游戏或存档。', parent=self.root):
            self.mc.delete_preset(name)
            self.refresh_presets()
            self._mod_log(f'已删除配置「{name}」。')

    def play_preset(self):
        for var in self.mod_vars.values():
            var.set(True)
        self.enemy_scale.set('0.5')
        self.equipment_scale.set('3')
        self._mod_log('已填入爽玩预设：商店 1 元、敌人 ×0.5、满额掉落、装备 ×3、技能零消耗。')
        self._mod_log('点击“应用到游戏”后生效。')

    def choose_game(self):
        path = filedialog.askdirectory(parent=self.root, title='选择 Game.exe 所在的游戏目录')
        if path:
            self.game_path.set(path)
            self._save_workshop_preferences(game_dir=path)
            self.mod_status()
            self._refresh_game_language(choose=True)

    def _mod_settings(self, require_game=True):
        if not self.mc:
            raise ValueError('游戏数据模块不可用')
        game = self.game_path.get().strip().strip('"')
        if require_game and not os.path.isfile(os.path.join(game, 'Game.exe')):
            raise ValueError('请先选择 Game.exe 所在的游戏目录')
        chosen = [key for key, var in self.mod_vars.items() if var.get()]
        enemy = self.mc.parse_scale(self.enemy_scale.get(), self.mc.SCALES)
        equip = self.mc.parse_scale(self.equipment_scale.get(), self.mc.EQUIP_SCALES)
        return game, chosen, enemy, equip

    def _run_mod_job(self, operation, completion='处理完成，请重新启动游戏'):
        if self.busy:
            return
        self.busy = True
        self.mod_apply_button.configure(state='disabled')
        self.mod_restore_button.configure(state='disabled')
        self.launch_button.configure(state='disabled')
        self.game_language_box.configure(state='disabled')
        self.launch_browse_button.configure(state='disabled')
        self.launch_path_entry.configure(state='disabled')
        self.mod_progress.pack(side='right', padx=(10, 0))
        self.mod_progress.start(12)
        self.status_var.set('正在处理游戏数据，请稍候…')
        def worker():
            try:
                operation(lambda message: self.events.put(('log', message)))
                self.events.put(('done', completion))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def apply_mods(self):
        try:
            game, chosen, enemy, equip = self._mod_settings()
        except ValueError as exc:
            messagebox.showerror('请检查设置', str(exc), parent=self.root)
            return
        def operation(log):
            log('开始应用配置 · 敌人 ×%g / 装备 ×%g' % (enemy, equip))
            with self._game_data_guard(game):
                if chosen:
                    self.mc.do_apply(game, chosen, enemy, equip, log)
                else:
                    self.mc.do_restore(game, log)
                state = self.mc.load_state()
                state.update(game=game, mods=chosen, scale=enemy, equip_scale=equip)
                self.mc.save_state(state)
        self._run_mod_job(operation)

    def restore_mods(self):
        if not self.mc:
            return
        game = self.game_path.get().strip().strip('"')
        if not os.path.isfile(os.path.join(game, 'Game.exe')):
            messagebox.showerror('请选择游戏', '请先选择 Game.exe 所在的游戏目录', parent=self.root)
            return
        def operation(log):
            with self._game_data_guard(game):
                self.mc.do_restore(game, log)
                state = self.mc.load_state()
                state.update(game=game, mods=[])
                self.mc.save_state(state)
        self._run_mod_job(operation)

    def _drain_events(self):
        try:
            while True:
                kind, message = self.events.get_nowait()
                if kind == 'log':
                    self._mod_log(message)
                elif kind == 'game_language':
                    self._save_workshop_preferences(game_language=message)
                else:
                    self.busy = False
                    self.mod_apply_button.configure(state='normal')
                    self.mod_restore_button.configure(state='normal')
                    self.launch_button.configure(state='normal')
                    self.game_language_box.configure(state='readonly')
                    self.launch_browse_button.configure(state='normal')
                    self.launch_path_entry.configure(state='normal')
                    self._refresh_game_language()
                    self.mod_progress.stop()
                    self.mod_progress.pack_forget()
                    if kind == 'error':
                        self._mod_log('处理失败：' + message)
                        self.say('操作未完成 · ' + message)
                    else:
                        self._mod_log(message)
                        self.say(message)
        except queue.Empty:
            pass
        try:
            self.root.after(120, self._drain_events)
        except tk.TclError:
            pass

    def mod_status(self):
        if not self.mc:
            return
        game = self.game_path.get().strip().strip('"')
        self._mod_log('— 当前状态 —')
        for line in self.mc.status_lines(game if os.path.isdir(game) else ''):
            self._mod_log(line)

    def close(self):
        if self.busy:
            messagebox.showinfo('正在处理', '游戏数据还在处理中，请完成后再关闭。', parent=self.root)
            return
        if self.drafts and not messagebox.askyesno('关闭工坊', '还有未生成的修改，确定关闭？', parent=self.root):
            return
        self.root.destroy()

    def run(self):
        args = sys.argv[1:]
        if '--game-root' in args:
            try:
                game=args[args.index('--game-root')+1]
                if os.path.isfile(os.path.join(game,'Game.exe')):
                    self.game_path.set(os.path.abspath(game))
                    self._refresh_game_language(choose=True)
            except IndexError:
                pass
        candidates = [a for a in args if not a.startswith('--') and not a.isdigit()]
        if candidates and os.path.isfile(candidates[0]):
            self.open_save(candidates[0])
        if '--tab' in args:
            try:
                self.show_page(max(0, min(6, int(args[args.index('--tab') + 1]))))
            except (ValueError, IndexError):
                pass
        self.root.mainloop()
