" mdt-cockpit system-wide Neovim default (installed as /etc/xdg/nvim/sysinit.vim).
"
" The NvChad tree lives beside this file in /etc/xdg/nvim. Neovim itself starts it:
" with no user init file it falls back to $XDG_CONFIG_DIRS/nvim/init.lua (XDG_CONFIG_DIRS
" defaults to /etc/xdg), so the home needs no copy and this file must NOT run the tree
" again (it used to `dofile` it, executing every start twice). A user's own
" ~/.config/nvim/init.lua (or init.vim) replaces the default entirely.
"
" What this file adds is the one thing the tree cannot do for itself: lazy.nvim keeps
" lazy-lock.json next to the config by default, and /etc/xdg/nvim is read-only for the user,
" so a starter-style tree's lockfile is redirected into the user's state directory BEFORE
" init.lua requires the module (it is cached in package.loaded, so init.lua sees the
" change). Only while the user has no configuration of their own: with one, the
" `configs.lazy` module on the runtimepath may be theirs.
if !filereadable(stdpath('config') . '/init.lua') && !filereadable(stdpath('config') . '/init.vim')
lua << EOF
vim.g.mdt_system_config = '/etc/xdg/nvim'
-- Absent when the tree has no configs/lazy module (the shipped core repository, MDT-007).
local ok, lazy_config = pcall(require, 'configs.lazy')
if ok and type(lazy_config) == 'table' then
  lazy_config.lockfile = vim.fn.stdpath('state') .. '/lazy-lock.json'
end
EOF
endif
