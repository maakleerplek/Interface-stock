use qrcode::{Color, QrCode};
use slint::{Rgb8Pixel, SharedPixelBuffer};

/// EPC/Wero payment QR content; banking apps read amount and IBAN from it.
pub fn epc_payload(name: &str, iban: &str, amount: f64, description: &str) -> String {
    // The remittance text is capped at 140 characters; apps reject longer.
    let description: String = description.chars().take(140).collect();
    let amount = format!("EUR{amount:.2}");
    ["BCD", "002", "1", "SCT", "", name, iban, &amount, "", "", &description].join("\n")
}

/// One pixel per module plus a 2-module quiet zone. The UI scales it up
/// with `image-rendering: pixelated`, so the edges stay sharp.
pub fn qr_pixels(content: &str) -> Option<SharedPixelBuffer<Rgb8Pixel>> {
    let code = QrCode::new(content.as_bytes()).ok()?;
    let (w, quiet) = (code.width(), 2);
    let side = (w + 2 * quiet) as u32;
    let mut buf = SharedPixelBuffer::<Rgb8Pixel>::new(side, side);
    let white = Rgb8Pixel { r: 255, g: 255, b: 255 };
    let black = Rgb8Pixel { r: 0, g: 0, b: 0 };
    let px = buf.make_mut_slice();
    px.fill(white);
    for (i, c) in code.to_colors().iter().enumerate() {
        if *c == Color::Dark {
            let (x, y) = (i % w + quiet, i / w + quiet);
            px[y * side as usize + x] = black;
        }
    }
    Some(buf)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn payload_has_eleven_lines_and_capped_text() {
        let p = epc_payload("HTL", "BE00", 3.4, &"x".repeat(200));
        let lines: Vec<_> = p.split('\n').collect();
        assert_eq!(lines.len(), 11);
        assert_eq!(lines[7], "EUR3.40");
        assert_eq!(lines[10].len(), 140);
        assert!(qr_pixels(&p).is_some());
    }
}
