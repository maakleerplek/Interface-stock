//! Link to the TV page (Tv-Presentation, hooks/useTvPage.ts), which runs in
//! Chromium on this same Pi. The Python version pressed keys in that
//! browser with xdotool; under Wayland that no longer works, and a key
//! would land in the focused window anyway. Instead the page connects to a
//! WebSocket here and gets one small JSON message per change:
//! `{"type":"busy"}`, `{"type":"idle"}`, `{"type":"next"}`, `{"type":"prev"}`.

use crate::state::TvPage;
use std::net::{TcpListener, TcpStream};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tungstenite::{Message, WebSocket};

/// The busy/idle state is repeated this often, so a TV that missed a
/// message (page reload, Wi-Fi hiccup) catches up, and a stuck "busy"
/// cannot freeze the TV: the page treats a silent busy kiosk as idle.
pub const HEARTBEAT: Duration = Duration::from_secs(30);

pub const DEFAULT_ADDR: &str = "127.0.0.1:8765";

#[derive(Default)]
struct Inner {
    clients: Vec<WebSocket<TcpStream>>,
    /// Last reported state; None until the kiosk reported once.
    busy: Option<bool>,
}

#[derive(Clone)]
pub struct Tv {
    inner: Arc<Mutex<Inner>>,
}

fn msg(kind: &str) -> Message {
    Message::text(format!("{{\"type\":\"{kind}\"}}"))
}

impl Inner {
    /// Send to every client; drop the ones that are gone.
    fn broadcast(&mut self, m: &Message) {
        self.clients.retain_mut(|ws| ws.send(m.clone()).is_ok());
    }
}

impl Tv {
    /// Listen on `addr` (localhost only by default: the TV browser runs on
    /// the same Pi). The kiosk keeps working if the port is taken.
    pub fn start(addr: &str) -> Self {
        let tv = Tv { inner: Default::default() };
        match TcpListener::bind(addr) {
            Ok(listener) => {
                eprintln!("[tv] WebSocket on ws://{addr}");
                let accept = tv.clone();
                std::thread::spawn(move || {
                    for stream in listener.incoming().flatten() {
                        let _ = stream.set_nodelay(true);
                        // A client that never finishes the handshake must not
                        // block the next one.
                        let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
                        // A TV browser that stops reading (out of memory, the
                        // white screen) must not block the kiosk on a full send
                        // buffer: after this a send fails and the client is dropped.
                        let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
                        if let Ok(mut ws) = tungstenite::accept(stream) {
                            let mut inner = accept.inner.lock().unwrap();
                            if let Some(b) = inner.busy {
                                let _ = ws.send(msg(if b { "busy" } else { "idle" }));
                            }
                            inner.clients.push(ws);
                        }
                    }
                });
                let beat = tv.clone();
                std::thread::spawn(move || loop {
                    std::thread::sleep(HEARTBEAT);
                    let mut inner = beat.inner.lock().unwrap();
                    if let Some(b) = inner.busy {
                        inner.broadcast(&msg(if b { "busy" } else { "idle" }));
                    }
                });
            }
            Err(e) => eprintln!("[tv] cannot listen on {addr}: {e} (TV control off)"),
        }
        tv
    }

    /// Report busy (someone shopping) or idle; sends only on a change, the
    /// heartbeat repeats it.
    pub fn set_busy(&self, busy: bool) {
        let mut inner = self.inner.lock().unwrap();
        if inner.busy != Some(busy) {
            inner.busy = Some(busy);
            inner.broadcast(&msg(if busy { "busy" } else { "idle" }));
        }
    }

    pub fn page(&self, page: TvPage) {
        let m = msg(match page {
            TvPage::Next => "next",
            TvPage::Prev => "prev",
        });
        self.inner.lock().unwrap().broadcast(&m);
    }

    pub fn clients(&self) -> usize {
        self.inner.lock().unwrap().clients.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn read_type(ws: &mut WebSocket<tungstenite::stream::MaybeTlsStream<TcpStream>>) -> String {
        let m = ws.read().unwrap();
        let v: serde_json::Value = serde_json::from_str(m.to_text().unwrap()).unwrap();
        v["type"].as_str().unwrap().to_string()
    }

    #[test]
    fn page_and_busy_reach_the_page() {
        let addr = "127.0.0.1:18765";
        let tv = Tv::start(addr);
        tv.set_busy(true);
        let (mut ws, _) = tungstenite::connect(format!("ws://{addr}")).unwrap();
        // A new client gets the current state straight away.
        assert_eq!(read_type(&mut ws), "busy");
        while tv.clients() == 0 {
            std::thread::sleep(Duration::from_millis(10));
        }
        tv.page(TvPage::Next);
        assert_eq!(read_type(&mut ws), "next");
        tv.set_busy(true); // no change: nothing sent
        tv.set_busy(false);
        assert_eq!(read_type(&mut ws), "idle");
        tv.page(TvPage::Prev);
        assert_eq!(read_type(&mut ws), "prev");
        drop(ws);
        // A closed client is dropped on the next send.
        for _ in 0..50 {
            tv.page(TvPage::Next);
            if tv.clients() == 0 {
                break;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        assert_eq!(tv.clients(), 0);
    }
}
