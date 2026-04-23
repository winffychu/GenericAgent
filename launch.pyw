import webview, threading, subprocess, sys, time, os, ctypes, atexit, socket, random, shutil

WINDOW_WIDTH, WINDOW_HEIGHT, RIGHT_PADDING, TOP_PADDING = 600, 900, 0, 100

script_dir = os.path.dirname(os.path.abspath(__file__))
frontends_dir = os.path.join(script_dir, "frontends")
proc = None

_instance_lock_fp = None


def acquire_single_instance_lock():
    global _instance_lock_fp
    lock_path = os.path.join(script_dir, '.launch.lock')
    _instance_lock_fp = open(lock_path, 'a+')
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(_instance_lock_fp.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_instance_lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        _instance_lock_fp.close()
        _instance_lock_fp = None
        return False
    _instance_lock_fp.seek(0)
    _instance_lock_fp.truncate()
    _instance_lock_fp.write(str(os.getpid()))
    _instance_lock_fp.flush()
    atexit.register(release_single_instance_lock)
    return True


def release_single_instance_lock():
    global _instance_lock_fp
    if not _instance_lock_fp:
        return
    try:
        if os.name == 'nt':
            import msvcrt
            _instance_lock_fp.seek(0)
            msvcrt.locking(_instance_lock_fp.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(_instance_lock_fp.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        _instance_lock_fp.close()
    finally:
        _instance_lock_fp = None


def find_free_port(lo=18501, hi=18599):
    ports = list(range(lo, hi+1)); random.shuffle(ports)
    for p in ports:
        try: s = socket.socket(); s.bind(('127.0.0.1', p)); s.close(); return p
        except OSError: continue
    raise RuntimeError(f'No free port in {lo}-{hi}')

def get_screen_width():
    try: return ctypes.windll.user32.GetSystemMetrics(0)
    except: return 1920

def start_streamlit(port):
    global proc
    cmd = [sys.executable, "-m", "streamlit", "run", os.path.join(frontends_dir, "stapp.py"), "--server.port", str(port), "--server.address", "0.0.0.0", "--server.headless", "true"]
    proc = subprocess.Popen(cmd)
    atexit.register(proc.kill)


def open_in_chrome(url):
    candidates = []
    if os.name == 'nt':
        local = os.environ.get('LOCALAPPDATA', '')
        program_files = os.environ.get('PROGRAMFILES', '')
        program_files_x86 = os.environ.get('PROGRAMFILES(X86)', '')
        candidates.extend([
            os.path.join(program_files, 'Google', 'Chrome', 'Application', 'chrome.exe'),
            os.path.join(program_files_x86, 'Google', 'Chrome', 'Application', 'chrome.exe'),
            os.path.join(local, 'Google', 'Chrome', 'Application', 'chrome.exe'),
            os.path.join(program_files, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
            os.path.join(program_files_x86, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        ])
    else:
        for name in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'microsoft-edge'):
            path = shutil.which(name)
            if path:
                candidates.append(path)

    browser = next((p for p in candidates if p and os.path.exists(p)), None)
    if not browser:
        raise RuntimeError('[Launch] Chrome/Chromium not found')

    subprocess.Popen([browser, '--incognito', url])


def inject(text):
    window.evaluate_js(f"""
        const textarea = document.querySelector('textarea[data-testid="stChatInputTextArea"]');
        if (textarea) {{
            // 1. 用原生 setter 设置值（绕过 React）
            const nativeTextAreaValueSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
            nativeTextAreaValueSetter.call(textarea, {repr(text)});
            // 2. 触发 React 的 input 事件
            textarea.dispatchEvent(new Event('input', {{ bubbles: true }}));
            // 3. 触发 change 事件（有些组件需要）
            textarea.dispatchEvent(new Event('change', {{ bubbles: true }}));
            // 4. 延迟提交
            setTimeout(() => {{
                const btn = document.querySelector('[data-testid="stChatInputSubmitButton"]');
                if (btn) {{btn.click();console.log('Submitted:', {repr(text)});}}
            }}, 200);
        }}""")

def get_last_reply_time():
    last = window.evaluate_js("""
        const el = document.getElementById('last-reply-time');
        el ? parseInt(el.textContent) : 0;
    """) or 0
    return last or int(time.time())

PASTE_HOOK_JS = """if (!window._pasteHooked) { window._pasteHooked = true;
    document.addEventListener('paste', e => {
        const items = e.clipboardData?.items; if (!items) return;
        let t = null;
        for (const item of items) { if (item.kind === 'file') { t = item.type.startsWith('image/') ? 'image in clipboard, ' : 'file in clipboard, '; break; } }
        if (!t) return;
        e.preventDefault(); e.stopImmediatePropagation();
        const el = document.querySelector('textarea[data-testid="stChatInputTextArea"]') || document.activeElement;
        if (el && (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT')) {
            const s = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
            s.call(el, el.value + t); el.dispatchEvent(new Event('input', { bubbles: true }));
        }
    }, true);
}"""

def idle_monitor():
    last_trigger_time = 0
    while True:
        time.sleep(5)
        try:
            window.evaluate_js(PASTE_HOOK_JS)
            now = time.time()
            if now - last_trigger_time < 120: continue
            last_reply = get_last_reply_time()
            if now - last_reply > 1800:
                print('[Idle Monitor] Detected idle state, injecting task...')
                inject("[AUTO]🤖 用户已经离开超过30分钟，作为自主智能体，请阅读自动化sop，执行自动任务。")
                last_trigger_time = now
        except Exception as e:
            print(f'[Idle Monitor] Error: {e}')

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('port', nargs='?', default='0'); 
    parser.add_argument('--tg', action='store_true', help='启动 Telegram Bot'); 
    parser.add_argument('--qq', action='store_true', help='启动 QQ Bot');
    parser.add_argument('--feishu', '--fs', dest='feishu', action='store_true', help='启动 Feishu Bot');
    parser.add_argument('--wecom', action='store_true', help='启动 WeCom Bot');
    parser.add_argument('--dingtalk', '--dt', dest='dingtalk', action='store_true', help='启动 DingTalk Bot');
    parser.add_argument('--sched', action='store_true', help='启动计划任务调度器')
    parser.add_argument('--llm_no', type=int, default=0, help='LLM编号')
    parser.add_argument('--gui', action='store_true', help='使用桌面窗口打开')
    args = parser.parse_args()

    if not acquire_single_instance_lock():
        print('[Launch] GenericAgent is already running')
        sys.exit(0)
    port = str(find_free_port()) if args.port == '0' else args.port
    print(f'[Launch] Using port {port}')
    threading.Thread(target=start_streamlit, args=(port,), daemon=True).start()

    if args.tg:
        tgproc = subprocess.Popen([sys.executable, os.path.join(frontends_dir, "tgapp.py")], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        atexit.register(tgproc.kill)
        print('[Launch] Telegram Bot started')
    else: print('[Launch] Telegram Bot not enabled (use --tg to start)')

    if args.qq:
        qqproc = subprocess.Popen([sys.executable, os.path.join(frontends_dir, "qqapp.py")], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        atexit.register(qqproc.kill)
        print('[Launch] QQ Bot started')
    else: print('[Launch] QQ Bot not enabled (use --qq to start)')

    if args.feishu:
        fsproc = subprocess.Popen([sys.executable, os.path.join(frontends_dir, "fsapp.py")], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        atexit.register(fsproc.kill)
        print('[Launch] Feishu Bot started')
    else: print('[Launch] Feishu Bot not enabled (use --feishu to start)')

    if args.wecom:
        wcproc = subprocess.Popen([sys.executable, os.path.join(frontends_dir, "wecomapp.py")], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        atexit.register(wcproc.kill)
        print('[Launch] WeCom Bot started')
    else: print('[Launch] WeCom Bot not enabled (use --wecom to start)')

    if args.dingtalk:
        dtproc = subprocess.Popen([sys.executable, os.path.join(frontends_dir, "dingtalkapp.py")], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        atexit.register(dtproc.kill)
        print('[Launch] DingTalk Bot started')
    else: print('[Launch] DingTalk Bot not enabled (use --dingtalk to start)')
    
    if args.sched:
        scheduler_proc = subprocess.Popen([sys.executable, os.path.join(script_dir, "agentmain.py"), "--reflect", os.path.join(script_dir, "reflect", "scheduler.py"), "--llm_no", str(args.llm_no)], creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        atexit.register(scheduler_proc.kill)
        print('[Launch] Task Scheduler started (duplicate prevented by scheduler port lock)')
    else: print('[Launch] Task Scheduler not enabled (--sched)')

    url = f'http://localhost:{port}'
    if args.gui:
        monitor_thread = threading.Thread(target=idle_monitor, daemon=True)
        monitor_thread.start()
        if os.name == 'nt':
            screen_width = get_screen_width()
            x_pos = screen_width - WINDOW_WIDTH - RIGHT_PADDING
        else:
            x_pos = 100
        time.sleep(2)
        window = webview.create_window(
            title='GenericAgent', url=url,
            width=WINDOW_WIDTH, height=WINDOW_HEIGHT, x=x_pos, y=TOP_PADDING,
            resizable=True, text_select=True)
        webview.start()
    else:
        print('[Launch] Defaulting to Chrome incognito; use --gui for desktop window')
        open_in_chrome(url)
        try:
            while proc is None or proc.poll() is None:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
