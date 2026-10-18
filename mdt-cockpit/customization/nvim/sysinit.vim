" mdt-cockpit system-wide Neovim default (installed as /etc/xdg/nvim/sysinit.vim).
"
" The NvChad tree lives beside this file in /etc/xdg/nvim, which is on every
" user's runtimepath (XDG_CONFIG_DIRS defaults to /etc/xdg), so the home needs no
" copy of it. Neovim only auto-loads init.lua from the user's own config
" directory, so this file starts the system tree - but only while the user has no
" configuration of their own: creating ~/.config/nvim/init.lua (or init.vim)
" replaces the default entirely.
if !filereadable(stdpath('config') . '/init.lua') && !filereadable(stdpath('config') . '/init.vim')
lua << EOF
vim.g.mdt_system_config = '/etc/xdg/nvim'
-- lazy.nvim keeps lazy-lock.json next to the config by default; the system tree
-- is not writable by the user, so a starter-style config redirects it into the
-- user's state directory. Absent when the tree has no configs/lazy module.
local ok, lazy_config = pcall(require, 'configs.lazy')
if ok and type(lazy_config) == 'table' then
  lazy_config.lockfile = vim.fn.stdpath('state') .. '/lazy-lock.json'
end
dofile('/etc/xdg/nvim/init.lua')
EOF
endif
