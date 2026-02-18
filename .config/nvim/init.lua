-- lazy packet manager
require("config.lazy")

-- relative number
vim.cmd('set number relativenumber')

-- <leader> key
vim.g.mapleader = ' '

-- cut
vim.cmd('nmap <leader>c "+d')

-- save
vim.cmd('nmap <leader>s :w<cr>')

-- close
vim.cmd('nmap <leader>w :q<cr>')

-- use blackhole register for action
vim.cmd('nmap <leader>f "_')

-- get file name
vim.cmd('nmap <leader>n :let @+ = expand(\'%:t\')<CR>')

-- go to definition
vim.keymap.set('n', '<leader>d', 'gd')

-- second word
vim.keymap.set('n', '<leader>t', '0w')

-- paste without overwriting
vim.keymap.set('v', 'p', 'P')

-- clear search highlighting
vim.keymap.set('n', '<Esc>', ':nohlsearch<cr>')

-- very magic search
vim.keymap.set('n', '\\', '/\\V', { noremap = true })

-- skip folds (down, up)
vim.cmd('nmap j gj')
vim.cmd('nmap k gk')

-- search ignoring case
vim.opt.ignorecase = true

-- disable "ignorecase" option if the search pattern contains upper case characters
vim.opt.smartcase = true

-- firenvim extension
vim.g.firenvim_config = {
    globalSettings = { alt = "all" },
    localSettings = {
        [".*"] = {
            cmdline  = "neovim",
            content  = "text",
            priority = 0,
            selector = "textarea",
            takeover = "never"
        }
    }
}

-- keep clean cmd after use
vim.api.nvim_create_autocmd("CmdlineLeave", {
    callback = function()
        vim.defer_fn(function()
            vim.cmd("echo ''")
        end, 10)
    end,
})

-- smart yank
  local map = vim.api.nvim_set_keymap
  local opts = { noremap = true, silent = true, expr = true }
  
  -- Map clipboard operations if no register is specified
  for _, mode in ipairs({ 'n', 'v', 'x' }) do
      for _, key in ipairs({ 'y', 'Y', 'p', 'P' }) do
          map(mode, key, string.format([[v:register == '"' ? '"+%s' : '%s']], key, key), opts)
      end
  end
  
  -- Highlight on yank
  vim.api.nvim_create_autocmd("TextYankPost", {
      group = vim.api.nvim_create_augroup("GlobalYankClipboard", { clear = true }),
      pattern = "*",
      callback = function()
          vim.highlight.on_yank({ higroup = "IncSearch", timeout = 150 })
      end,
  })

-- Vim-easy-align
vim.cmd('xmap ga <Plug>(EasyAlign)')

vim.cmd('nmap ga <Plug>(EasyAlign)')

-- Assign search
vim.keymap.set('n', '<leader>a', function()
  -- grab the word under the cursor
  local word = vim.fn.expand('<cword>')
  -- escape any magic chars in the word
  local esc = vim.fn.escape(word, '\\/.*$^~[]')
  -- build a very-magic pattern with lookbehind/lookahead
  local pat = '\\vassign \\zs' .. esc .. '[ .]+\\ze ='
  -- set it as the last search pattern
  vim.fn.setreg('/', pat)
  -- jump to the next match
  vim.cmd('normal! n')
end, { noremap = true, silent = true })
