//! Small parsing and formatting helpers (no allocation on the hot path).
#[cfg(not(all(target_arch = "x86_64", target_os = "linux")))]
use std::fs::File;
#[cfg(not(all(target_arch = "x86_64", target_os = "linux")))]
use std::io::Read;

pub const NONE: u64 = u64::MAX;

pub fn parse_u64(b: &[u8]) -> Option<u64> {
    if b.is_empty() {
        return None;
    }
    let mut v: u64 = 0;
    for &c in b {
        if !c.is_ascii_digit() {
            return None;
        }
        v = v.checked_mul(10)?.checked_add((c - b'0') as u64)?;
    }
    Some(v)
}

pub fn parse_f64(b: &[u8]) -> Option<f64> {
    std::str::from_utf8(b).ok()?.parse().ok()
}

pub fn lines(b: &[u8]) -> impl Iterator<Item = &[u8]> {
    b.split(|&c| c == b'\n').filter(|l| !l.is_empty())
}

pub fn fields(b: &[u8]) -> impl Iterator<Item = &[u8]> {
    b.split(|&c| c == b' ' || c == b'\t').filter(|f| !f.is_empty())
}

pub fn trim(b: &[u8]) -> &[u8] {
    let mut s = 0;
    let mut e = b.len();
    while s < e && b[s].is_ascii_whitespace() {
        s += 1;
    }
    while e > s && b[e - 1].is_ascii_whitespace() {
        e -= 1;
    }
    &b[s..e]
}

/// One open, one read into a large reusable buffer, one close. A pseudo-file read that
/// does not fill the buffer is complete (sysfs/procfs single_open/seq_file semantics).
///
/// On x86_64 Linux the three calls are raw syscalls: musl's `open()` issues an extra
/// `fcntl(F_SETFD)` after every `openat(O_CLOEXEC)`, which is a quarter of all syscalls
/// of a tick (measured with strace). Elsewhere std::fs is used.
pub struct Slurp {
    buf: Vec<u8>,
}

#[cfg(all(target_arch = "x86_64", target_os = "linux"))]
mod raw {
    use std::arch::asm;
    unsafe fn sc3(n: isize, a: usize, b: usize, c: usize) -> isize {
        let r: isize;
        asm!("syscall", inlateout("rax") n => r, in("rdi") a, in("rsi") b, in("rdx") c,
             lateout("rcx") _, lateout("r11") _, options(nostack));
        r
    }
    pub fn open_ro(path: &str) -> Result<i32, i32> {
        let mut b = [0u8; 512];
        if path.len() >= b.len() {
            return Err(36);
        }
        b[..path.len()].copy_from_slice(path.as_bytes());
        // openat(AT_FDCWD, path, O_RDONLY | O_CLOEXEC)
        let r = unsafe { sc3(257, (-100isize) as usize, b.as_ptr() as usize, 0o2000000) };
        if r < 0 { Err((-r) as i32) } else { Ok(r as i32) }
    }
    pub fn read(fd: i32, buf: &mut [u8]) -> Result<usize, i32> {
        let r = unsafe { sc3(0, fd as usize, buf.as_mut_ptr() as usize, buf.len()) };
        if r < 0 { Err((-r) as i32) } else { Ok(r as usize) }
    }
    pub fn close(fd: i32) {
        unsafe { sc3(3, fd as usize, 0, 0) };
    }
}

impl Slurp {
    pub fn new() -> Self {
        Slurp { buf: vec![0u8; 256 * 1024] }
    }

    #[cfg(all(target_arch = "x86_64", target_os = "linux"))]
    pub fn read(&mut self, path: &str) -> std::io::Result<&[u8]> {
        let fd = raw::open_ro(path).map_err(std::io::Error::from_raw_os_error)?;
        let mut n = 0;
        let mut err = None;
        loop {
            match raw::read(fd, &mut self.buf[n..]) {
                Ok(k) => {
                    n += k;
                    if k == 0 || n < self.buf.len() {
                        break;
                    }
                    let l = self.buf.len();
                    self.buf.resize(l * 2, 0);
                }
                Err(e) => {
                    err = Some(e);
                    break;
                }
            }
        }
        raw::close(fd);
        match err {
            Some(e) => Err(std::io::Error::from_raw_os_error(e)),
            None => Ok(&self.buf[..n]),
        }
    }

    #[cfg(not(all(target_arch = "x86_64", target_os = "linux")))]
    pub fn read(&mut self, path: &str) -> std::io::Result<&[u8]> {
        let mut f = File::open(path)?;
        let mut n = 0;
        loop {
            let k = f.read(&mut self.buf[n..])?;
            n += k;
            if k == 0 || n < self.buf.len() {
                break;
            }
            let l = self.buf.len();
            self.buf.resize(l * 2, 0);
        }
        Ok(&self.buf[..n])
    }
}

pub fn jstr(o: &mut String, s: &str) {
    o.push('"');
    for c in s.chars() {
        match c {
            '"' => o.push_str("\\\""),
            '\\' => o.push_str("\\\\"),
            '\n' => o.push_str("\\n"),
            c if (c as u32) < 0x20 => o.push_str(&format!("\\u{:04x}", c as u32)),
            c => o.push(c),
        }
    }
    o.push('"');
}

pub fn jnum(o: &mut String, v: f64) {
    use std::fmt::Write;
    if v.is_finite() {
        let _ = write!(o, "{}", v);
    } else {
        o.push_str("null");
    }
}

pub fn wall_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}
