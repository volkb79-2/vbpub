"""Interactive-user ergonomics (rc files, aliases) applied to root and /etc/skel.

Ported from scripts/debian-install/configure-users.sh (legacy v1) after a
general-server sanity review. Dropped from the legacy content, on purpose:
  * iftop ``sort: 2bit`` and ``num-lines`` -- not valid iftop directives;
  * htop ``fields=``/``sort_key=``/``tree_sort_key=`` -- numeric column ids are
    htop-version specific, so htop keeps its own defaults for them;
  * the ``fgrep``/``egrep`` aliases -- those commands print an "obsolescent"
    warning on every call on current grep;
  * v1's unconditional ``.profile`` rewrite -- Debian's own skel ``.profile``
    already sources ``.bashrc``.
"""

from __future__ import annotations

NANORC = """\
# Nano Configuration
set tabsize 4
set softwrap
set tabstospaces
set mouse
set linenumbers
set smooth
set autoindent
set boldtext
include /usr/share/nano/*.nanorc
"""

MC_INI = """\
[Midnight-Commander]
skin=modarin256-defbg-thin
shadows=false
use_internal_view=true
use_internal_edit=true
auto_save_setup=true
pause_after_run=1
shell_patterns=true
auto_menu=false
drop_menus=false
wrap_mode=true
confirm_delete=true
confirm_overwrite=true
confirm_execute=true
confirm_exit=false
safe_delete=false
mouse_repeat_rate=100
double_click_speed=250
old_esc_mode=false
cd_follows_links=true
safe_overwrite=false

[Layout]
message_visible=true
keybar_visible=true
xterm_title=true
output_lines=0
command_prompt=true
menubar_visible=true
free_space=true

[Misc]
display_codepage=UTF-8
source_codepage=Other_8_bit
"""

_MC_PANEL = """\
display=listing
reverse=false
case_sensitive=true
exec_first=false
sort_order=name
list_mode=full
brief_cols=2
user_format=half type name | size | owner | group | perm | atime
user_mini_status=false
filter_flags=7
list_format=user
"""

MC_PANELS = (
    "[New Left Panel]\n" + _MC_PANEL
    + "\n[New Right Panel]\n" + _MC_PANEL
    + "\n[Dirs]\ncurrent_is_left=true\n"
)

IFTOPRC = """\
# iftop Configuration
show-bars: yes
port-resolution: yes
dns-resolution: no
show-totals: yes
log-scale: no
line-display: two-line
port-display: on
"""

HTOPRC = """\
# htop Configuration
hide_kernel_threads=1
hide_userland_threads=0
shadow_other_users=0
show_thread_names=0
show_program_path=1
highlight_base_name=1
highlight_deleted_exe=1
shadow_distribution_path_prefix=0
highlight_megabytes=1
highlight_threads=1
highlight_changes=0
highlight_changes_delay_secs=5
find_comm_in_cmdline=1
strip_exe_from_cmdline=1
show_merged_command=0
header_margin=1
screen_tabs=1
detailed_cpu_time=0
cpu_count_from_one=0
show_cpu_usage=1
show_cpu_frequency=0
show_cpu_temperature=0
degree_fahrenheit=0
update_process_names=0
account_guest_in_cpu_meter=0
color_scheme=0
enable_mouse=1
delay=15
hide_function_bar=0
header_layout=two_50_50
column_meters_0=AllCPUs Memory Swap
column_meter_modes_0=1 1 1
column_meters_1=Tasks LoadAverage Uptime
column_meter_modes_1=2 2 2
tree_view=0
tree_view_always_by_pid=0
all_branches_collapsed=0
"""

TOPRC = """\
top's Config File (Linux processes with windows)
Id:k, Mode_altscr=0, Mode_irixps=1, Delay_time=2.0, Curwin=0
Def     fieldscur=  75  107   81  103  105  119  163  123  121  129  161  159  136  111  223  117  115  220   76   78
                    82   84   86   88   90   92   94   96   98  100  108  112  124  126  130  132  206  210  134  208
                   212  140  142  146  148  150  152  154  164  166  168  170  172  174  176  178  180  182  184  194
                   188  139  187  144  157  190  192  196  198  200  202  204  214  216  218  224  226  228  230  232
                   234  236  238  240  242  244  246  248  250  252  254  256  258  260  262  264  266  268  270  272
        winflags=671540, sortindx=18, maxtasks=0, graph_cpus=0, graph_mems=0, double_up=0, combine_cpus=0, core_types=0
        summclr=6, msgsclr=1, headclr=4, taskclr=4
Job     fieldscur=  75   77  115  111  117   80  103  105  137  119  123  128  120   79  139   82   84   86   88   90
                    92   94   96   98  100  106  108  112  124  126  130  132  134  140  142  144  146  148  150  152
                   154  156  158  160  162  164  166  168  170  172  174  176  178  180  182  184  186  188  190  192
                   194  196  198  200  202  204  206  208  210  212  214  216  218  220  222  224  226  228  230  232
                   234  236  238  240  242  244  246  248  250  252  254  256  258  260  262  264  266  268  270  272
        winflags=193844, sortindx=0, maxtasks=0, graph_cpus=0, graph_mems=0, double_up=0, combine_cpus=0, core_types=0
        summclr=6, msgsclr=6, headclr=7, taskclr=6
Mem     fieldscur=  75  117  119  120  123  125  127  129  131  154  132  156  135  136  102  104  111  139   76   78
                    80   82   84   86   88   90   92   94   96   98  100  106  108  112  114  140  142  144  146  148
                   150  152  158  160  162  164  166  168  170  172  174  176  178  180  182  184  186  188  190  192
                   194  196  198  200  202  204  206  208  210  212  214  216  218  220  222  224  226  228  230  232
                   234  236  238  240  242  244  246  248  250  252  254  256  258  260  262  264  266  268  270  272
        winflags=193844, sortindx=21, maxtasks=0, graph_cpus=0, graph_mems=0, double_up=0, combine_cpus=0, core_types=0
        summclr=5, msgsclr=5, headclr=4, taskclr=5
Usr     fieldscur=  75   77   79   81   85   97  115  111  117  137  139   82   86   88   90   92   94   98  100  102
                   104  106  108  112  118  120  122  124  126  128  130  132  134  140  142  144  146  148  150  152
                   154  156  158  160  162  164  166  168  170  172  174  176  178  180  182  184  186  188  190  192
                   194  196  198  200  202  204  206  208  210  212  214  216  218  220  222  224  226  228  230  232
                   234  236  238  240  242  244  246  248  250  252  254  256  258  260  262  264  266  268  270  272
        winflags=193844, sortindx=3, maxtasks=0, graph_cpus=0, graph_mems=0, double_up=0, combine_cpus=0, core_types=0
        summclr=3, msgsclr=3, headclr=2, taskclr=3
Fixed_widest=0, Summ_mscale=1, Task_mscale=1, Zero_suppress=0, Tics_scaled=0
"""

BASH_ALIASES = """\
# vbpub debian-install v2: interactive shell aliases
alias ll='ls -alF'
alias la='ls -A'
alias l='ls -CF'
alias ls='ls --color=auto'
alias grep='grep --color=auto'
alias df='df -h'
alias du='du -h'
alias free='free -h'

# Pretty-print a log file: turn carriage returns into newlines, keep ANSI colours.
catlog() { tr '\\r' '\\n' < "$1" | less -R; }
"""

# Appended to a .bashrc that does not already mention .bash_aliases.
BASHRC_ALIASES_SNIPPET = """\

# Source bash aliases if available
if [ -f ~/.bash_aliases ]; then
    . ~/.bash_aliases
fi
"""

# (path relative to the home/skel directory, content). Same set for root and skel.
USER_RC_FILES: tuple[tuple[str, str], ...] = (
    (".nanorc", NANORC),
    (".config/mc/ini", MC_INI),
    (".config/mc/panels.ini", MC_PANELS),
    (".iftoprc", IFTOPRC),
    (".config/htop/htoprc", HTOPRC),
    (".config/procps/toprc", TOPRC),
    (".bash_aliases", BASH_ALIASES),
)
