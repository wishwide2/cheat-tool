"""Game launch, language selection and story navigation in the workshop."""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
from queue import Empty, Queue
import subprocess
from threading import Thread
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

from launcher_backend import LanguageSwitcher, is_game_running, switch_lock
from story_navigation import CHAPTERS, scan_story_saves, write_story_save, write_story_start
from workshop_paths import program_directory

GAME_LANGUAGES = {'简体中文': 'zh', '日本語': 'ja', 'English': 'en'}
CURRENT_LANGUAGE = '当前安装的语言'


class GameLaunchFeatures:
    def _detect_game_directory(self):
        selected = self.game_path.get().strip().strip('"')
        if selected and (Path(selected) / 'Game.exe').is_file():
            return
        program = program_directory()
        recent = self.preferences.get('game_dir')
        candidates = [program, program.parent / 'akasan9.51']
        if isinstance(recent, str) and recent:
            candidates.insert(0, Path(recent))
        for candidate in candidates:
            if (candidate/'Game.exe').is_file():
                self.game_path.set(str(candidate))
                return

    def _build_launch_page(self):
        page = self.pages[6]
        self.game_language = tk.StringVar(value=CURRENT_LANGUAGE)
        self.game_language_status = tk.StringVar(value='请选择 Game.exe 所在的目录')
        self.game_language_note = tk.StringVar()
        panel, body = self.card(page, '启动本地游戏', '选择自己的游戏目录，使用本机已安装的资源启动。', padding=24)
        panel.pack(fill='x',pady=(0,16))
        path_row = ttk.Frame(body)
        path_row.pack(fill='x', pady=(18, 14))
        self.launch_path_entry = ttk.Entry(path_row, textvariable=self.game_path)
        self.launch_path_entry.pack(side='left', fill='x', expand=True, padx=(0, 12))
        self.launch_browse_button = ttk.Button(path_row, text='选择游戏目录', command=self.choose_game)
        self.launch_browse_button.pack(side='right')
        options = ttk.Frame(body)
        options.pack(fill='x', pady=(3, 8))
        ttk.Label(options, text='游戏语言', style='Strong.TLabel').pack(side='left', padx=(0, 14))
        self.game_language_box = ttk.Combobox(options, textvariable=self.game_language,
                                             values=(CURRENT_LANGUAGE,), state='disabled', width=16)
        self.game_language_box.pack(side='left')
        self.game_language_box.bind('<<ComboboxSelected>>', self._game_language_selected)
        self.launch_button = ttk.Button(options, text='启动游戏  →', style='Primary.TButton',
                                        command=self.launch_game)
        self.launch_button.pack(side='right')
        ttk.Label(body, textvariable=self.game_language_status, style='Muted.TLabel').pack(anchor='w', pady=(12, 0))
        ttk.Label(body, textvariable=self.game_language_note, style='Muted.TLabel',
                  wraplength=700).pack(anchor='w', pady=(6, 0))
        help_panel, help_card = self.card(page, '从这里完成设置', padding=20)
        help_panel.pack(fill='x')
        for label, text, index in [
            ('编辑存档', '打开存档，调整角色、资源、装备和技能，再生成新槽位。', 0),
            ('玩法设置', '选择价格、敌人、掉落、装备和技能规则，应用到游戏。', 5),
        ]:
            row = ttk.Frame(help_card)
            row.pack(fill='x', pady=9)
            ttk.Label(row, text=text, wraplength=560).pack(side='left', fill='x', expand=True)
            ttk.Button(row, text=label, command=lambda target=index: self.show_page(target)).pack(side='right', padx=(12, 0))
        story_row = ttk.Frame(help_card)
        story_row.pack(fill='x', pady=9)
        ttk.Label(story_row, text='从指定段落新开，或回读已有章节档。',
                  wraplength=560).pack(side='left', fill='x', expand=True)
        ttk.Button(story_row, text='剧情导航', command=self.show_story_navigation).pack(side='right', padx=(12, 0))
        ttk.Label(page, text='生成新存档后，在游戏中读取对应槽位。本地语言包可用时会显示对应选项。',
                  style='PageMuted.TLabel', wraplength=700).pack(anchor='w', padx=4, pady=10)
        self.root.bind('<Control-Return>', lambda event: self.launch_game())

    def show_story_navigation(self):
        if self.busy:
            return
        selected = self.game_path.get().strip().strip('"')
        if not selected or not (Path(selected) / 'Game.exe').is_file():
            messagebox.showinfo('选择游戏目录', '请先在「启动游戏」页选择 Game.exe 所在的目录。', parent=self.root)
            self.show_page(6)
            return
        game = Path(selected).resolve()
        window = self._dialog('剧情导航', width=900, height=650)
        outer = ttk.Frame(window, padding=20)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='选择要进入的剧情段落', style='CardTitle.TLabel').pack(anchor='w')
        ttk.Label(outer, text=f'{game / "Save"}', style='Muted.TLabel', wraplength=780).pack(anchor='w', pady=(4, 8))
        tree = self._dialog_table(outer, [('chapter', '章节'), ('start', '独立起点'),
                                         ('state', '记录状态'), ('count', '可用档数')],
                                  [285, 155, 155, 75])
        tree.configure(selectmode='browse')
        footer = ttk.Frame(outer)
        footer.pack(side='bottom', fill='x', pady=(10, 0), before=tree.master)
        footer.columnconfigure(1, weight=1)
        ttk.Label(footer, text='该段记录').grid(row=0, column=0, sticky='w', padx=(0, 9))
        source_box = ttk.Combobox(footer, state='disabled', width=35)
        source_box.grid(row=0, column=1, columnspan=5, sticky='ew')
        ttk.Label(footer, text='携带存档').grid(row=1, column=0, sticky='w', padx=(0, 9), pady=(7, 0))
        opened = Path(self.path).resolve() if self.path else None
        carry_from = opened
        source_digest = hashlib.sha256(self.raw).hexdigest() if opened else None
        carry_label = tk.StringVar(value='请选择要携带角色养成和资源的存档。')
        if opened:
            carry_label.set(f'{opened.name} · 当前已打开的存档'
                            + (' · 使用磁盘上已保存的数值' if self.drafts else ''))
        ttk.Label(footer, textvariable=carry_label, style='Muted.TLabel', wraplength=555).grid(
            row=1, column=1, columnspan=4, sticky='w', pady=(7, 0))
        status = tk.StringVar(value='正在读取本机存档…')
        ttk.Label(footer, textvariable=status, style='Muted.TLabel',
                  wraplength=780).grid(row=2, column=0, columnspan=6, sticky='w', pady=(7, 2))
        ttk.Label(footer, text='带档跳转会生成新槽位，继承角色养成和资源。任务、剧情道具和出场队伍随目的地。'
                              '「从该段新开」使用预设数值。',
                  style='Muted.TLabel', wraplength=780).grid(row=3, column=0, columnspan=6, sticky='w', pady=(0, 8))

        index = None
        sources = []
        working = writing = False

        def close(event=None):
            if not writing:
                window.destroy()

        def selected_start():
            selection = tree.selection()
            return index.starts.get(int(selection[0])) if index and selection else None

        def update_buttons(event=None):
            position = source_box.current()
            source = sources[position] if 0 <= position < len(sources) else None
            copy_button.configure(state='normal' if source and not working else 'disabled')
            can_register = (source and not working and source.slot is not None and index.has_title_menu
                            and source.chapter not in index.occupied_checkpoints)
            register_button.configure(state='normal' if can_register else 'disabled')
            can_start = not working and selected_start() is not None
            start_button.configure(state='normal' if can_start else 'disabled')
            jump_button.configure(state='normal' if can_start and carry_from else 'disabled')
            browse_button.configure(state='disabled' if working else 'normal')
            refresh_button.configure(state='disabled' if working else 'normal')

        def browse_carry():
            nonlocal carry_from, source_digest
            save_folder = game / 'Save'
            path = filedialog.askopenfilename(parent=window, title='选择携带存档',
                                              initialdir=carry_from.parent if carry_from else save_folder,
                                              filetypes=[('游戏存档', '*.sav'), ('全部文件', '*.*')])
            if path:
                carry_from = Path(path).resolve()
                source_digest = None
                carry_label.set(str(carry_from) + (
                    ' · 使用磁盘上已保存的数值' if carry_from == opened and self.drafts else ''))
                update_buttons()

        def select_chapter(event=None):
            nonlocal sources
            selection = tree.selection()
            sources = index.saves[int(selection[0])] if index and selection else []
            values = []
            for save in sources:
                kind = '章节档' if save.slot is None else f'普通档 {save.slot:02d}'
                updated = datetime.fromtimestamp(save.modified).strftime('%Y-%m-%d %H:%M')
                values.append(f'{kind} · {save.path.name} · {updated}')
            source_box.configure(values=values, state='readonly' if values else 'disabled')
            if values:
                source_box.current(0)
            else:
                source_box.set('')
            update_buttons()

        def show_index(value):
            nonlocal index
            index = value
            previous = tree.selection()
            tree.delete(*tree.get_children())
            for number, (name, _) in CHAPTERS.items():
                saves = index.saves[number]
                start = index.starts.get(number)
                has_checkpoint = any(save.slot is None for save in saves)
                if has_checkpoint:
                    state = '有章节档'
                elif number in index.occupied_checkpoints:
                    state = '章节档异常'
                else:
                    state = '有普通档' if saves else '暂无记录'
                start_label = f'主角 Lv.{start.level}' if start else '—'
                tree.insert('', 'end', iid=str(number),
                            values=(f'{number:02d}  {name}', start_label, state, len(saves)))
            first = previous[0] if previous else str(next((n for n in CHAPTERS if index.saves[n]), 1))
            tree.selection_set(first)
            tree.see(first)
            count = sum(bool(saves) for saves in index.saves.values())
            summary = f'{len(index.starts)} 段可跳转或新开，{count} 段有记录。'
            if index.unreadable:
                summary += f' 已跳过 {len(index.unreadable)} 个无法识别的文件。'
            if not index.has_title_menu:
                summary += ' 补录到标题需要兼容的游戏内剧情导航。'
            status.set(summary)
            select_chapter()

        def run(operation, on_success, *, write=False):
            nonlocal working, writing
            if working:
                return
            working, writing = True, write
            status.set('正在校验并写入存档…' if write else '正在读取本机存档…')
            update_buttons()
            result = Queue()

            def worker():
                try:
                    result.put((True, operation()))
                except Exception as exc:
                    result.put((False, exc))

            def poll():
                nonlocal working, writing
                try:
                    ok, value = result.get_nowait()
                except Empty:
                    window.after(60, poll)
                    return
                working = writing = False
                update_buttons()
                if ok:
                    on_success(value)
                else:
                    status.set(str(value))
                    messagebox.showerror('剧情导航', str(value), parent=window)

            Thread(target=worker, daemon=not write).start()
            window.after(60, poll)

        def refresh():
            run(lambda: scan_story_saves(game), show_index)

        def write_slot(operation, *, as_checkpoint=False):
            def save():
                with self._game_data_guard(game):
                    return operation()

            def saved(path):
                instruction = ('到游戏标题的「剧情导航 → 章节记录」读取。' if as_checkpoint
                               else '到游戏的「继续游戏」选择对应的新槽位。')
                self.say(f'已生成剧情存档 {path.name}')
                messagebox.showinfo('剧情存档已生成', f'已生成 {path.name}。\n{instruction}', parent=window)
                refresh()

            run(save, saved, write=True)

        def save_selected(as_checkpoint=False):
            position = source_box.current()
            if 0 <= position < len(sources):
                source = sources[position]
                write_slot(lambda: write_story_save(game, source, as_checkpoint=as_checkpoint),
                           as_checkpoint=as_checkpoint)

        def save_start(*, carry=False):
            start = selected_start()
            if start is not None and (not carry or carry_from):
                path, digest = (carry_from, source_digest) if carry else (None, None)
                write_slot(lambda: write_story_start(game, start, carry_from=path, source_digest=digest))

        browse_button = ttk.Button(footer, text='选择存档…', command=browse_carry)
        browse_button.grid(row=1, column=5, sticky='e', pady=(7, 0))
        refresh_button = ttk.Button(footer, text='刷新', command=refresh)
        refresh_button.grid(row=4, column=0, sticky='w')
        register_button = ttk.Button(footer, text='补录到游戏标题', command=lambda: save_selected(True))
        register_button.grid(row=4, column=2, padx=8)
        copy_button = ttk.Button(footer, text='生成新槽位', command=save_selected)
        copy_button.grid(row=4, column=3, padx=(0, 8))
        start_button = ttk.Button(footer, text='从该段新开', command=save_start)
        start_button.grid(row=4, column=4, padx=(0, 8))
        jump_button = ttk.Button(footer, text='带存档跳转', style='Primary.TButton',
                                 command=lambda: save_start(carry=True))
        jump_button.grid(row=4, column=5)
        window.protocol('WM_DELETE_WINDOW', close)
        window.bind('<Escape>', close)
        tree.bind('<<TreeviewSelect>>', select_chapter)
        source_box.bind('<<ComboboxSelected>>', update_buttons)
        refresh()
        self._finish_dialog(window)
        return window

    def _game_language_selected(self, event=None):
        code = GAME_LANGUAGES.get(self.game_language.get())
        self.game_language_note.set(
            'English 沿用原作提供的部分翻译，覆盖菜单和部分对话；未译内容保留日文。'
            if code == 'en' else '')

    def _refresh_game_language(self, choose=False):
        selected = self.game_path.get().strip().strip('"')
        game = Path(selected)
        options = [CURRENT_LANGUAGE]
        self.game_language_note.set('')
        self.game_language_status.set('将使用本机现有资源' if selected and (game / 'Game.exe').is_file()
                                      else '请选择 Game.exe 所在的目录')
        try:
            if not selected or not (game / 'Game.exe').is_file():
                return
            localization = game / 'Localization'
            manifest = json.loads((localization / 'manifest.json').read_text('utf-8'))
            modes = manifest['languages']
            with (game / 'Data' / 'MapData.wolf').open('rb') as handle:
                map_hash = hashlib.file_digest(handle, 'sha256').hexdigest()
            installed = next((code for code, language in modes.items()
                              if language['files']['MapData.wolf']['sha256'] == map_hash), None)
            options.extend(label for label, code in GAME_LANGUAGES.items() if code in modes)
            label = next((name for name, code in GAME_LANGUAGES.items() if code == installed), None)
            if label:
                self.game_language_status.set('当前游戏语言 · ' + label)
        except (OSError, AttributeError, KeyError, TypeError, ValueError):
            pass
        finally:
            self.game_language_box.configure(values=options, state='readonly' if len(options) > 1 else 'disabled')
            if choose or self.game_language.get() not in options:
                self.game_language.set(CURRENT_LANGUAGE)
            self._game_language_selected()

    @contextmanager
    def _game_data_guard(self, game):
        root = Path(game).resolve()
        lock = root / 'Localization' / '.launcher.lock'
        if not lock.parent.is_dir():
            lock = root / '.akasan_workshop.lock'
        with switch_lock(lock):
            if is_game_running(root / 'Game.exe'):
                raise RuntimeError('游戏正在运行，请先保存并退出，再操作游戏文件。')
            yield

    def launch_game(self):
        if self.busy:
            return
        selected = self.game_path.get().strip().strip('"')
        game = Path(selected).resolve()
        if not selected or not (game / 'Game.exe').is_file():
            messagebox.showerror('请选择游戏', '请选择 Game.exe 所在的游戏目录。', parent=self.root)
            return
        self._refresh_game_language()
        language = GAME_LANGUAGES.get(self.game_language.get())
        def operation(log):
            if language is None:
                engine = game / 'Game.exe'
                if is_game_running(engine):
                    raise RuntimeError('这个游戏已在运行。')
                subprocess.Popen([str(engine)], cwd=game)
            else:
                result = LanguageSwitcher(game, status=log).switch(language, launch=True)
                self.events.put(('game_language', result.language))
        self._run_mod_job(operation, completion='已发出启动请求，可在游戏中读取存档')
