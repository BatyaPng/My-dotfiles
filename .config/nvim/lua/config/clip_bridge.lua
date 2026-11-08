-- Clipboard over ssh: copy via OSC 52 (reaches the laptop's Wayland clipboard through
-- tmux + ssh + Alacritty) and via X11 xclip; paste via forwarded X, else the last yank.
local osc52 = require('vim.ui.clipboard.osc52')
local last = { ['+'] = { {}, 'v' }, ['*'] = { {}, 'v' } }

local function copy(reg)
  local osc = osc52.copy(reg)
  return function(lines, regtype)
    last[reg] = { lines, regtype }
    osc(lines)
    vim.system({ vim.fn.expand('~/.local/bin/xclip'), '-selection',
      reg == '+' and 'clipboard' or 'primary', '-i' }, { stdin = table.concat(lines, '\n'), timeout = 2000 })
  end
end

local function paste(reg)
  return function()
    local r = vim.system({ vim.fn.expand('~/.local/bin/xclip'), '-selection',
      reg == '+' and 'clipboard' or 'primary', '-o' }, { text = true, timeout = 1500 }):wait()
    if r.code == 0 and r.stdout ~= '' then
      return vim.split(r.stdout, '\n', { plain = true })
    end
    return last[reg]
  end
end

vim.g.clipboard = {
  name = 'osc52+xclip',
  copy = { ['+'] = copy('+'), ['*'] = copy('*') },
  paste = { ['+'] = paste('+'), ['*'] = paste('*') },
}
