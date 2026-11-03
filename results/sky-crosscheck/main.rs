#[allow(dead_code)]
#[path = "/Users/yukihamada/workspace/mu-house/src/tateru_engine.rs"]
mod tateru_engine;
mod cases;
use tateru_engine::*;
fn main() {
    for (id, px, py, bs) in cases::cases() {
        let solids: Vec<Solid> = bs.iter().map(|b| Solid {
            poly: vec![[b[0], b[2]], [b[1], b[2]], [b[1], b[3]], [b[0], b[3]]],
            cap: b[4], a: b[4], b: 0.0 }).collect();
        println!("{}\t{:.5}\t{:.5}", id, sky_ratio_at(160, px, py, &solids) * 100., sky_ratio_at(1200, px, py, &solids) * 100.);
    }
}
