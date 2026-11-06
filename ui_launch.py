"""Game-language controls in the save workshop."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
from tkinter import messagebox, ttk
import tkinter as tk

from launcher_backend import LanguageSwitcher, is_game_running, switch_lock
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
        ttk.Label(page, text='生成新存档后，在游戏中读取对应槽位。本地语言包可用时会显示对应选项。',
                  style='PageMuted.TLabel', wraplength=700).pack(anchor='w', padx=4, pady=10)
        self.root.bind('<Control-Return>', lambda event: self.launch_game())

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
                raise RuntimeError('游戏正在运行，请先保存并退出，再应用玩法设置。')
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
