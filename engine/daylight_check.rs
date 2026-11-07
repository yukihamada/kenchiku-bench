#[allow(dead_code)]
#[path = "../../mu-house/crates/houki-engine/src/lib.rs"]
mod houki_engine;

use houki_engine::daylight_coefficient;
use std::io::Read;

fn main() {
    let mut s = String::new();
    std::io::stdin().read_to_string(&mut s).unwrap();
    let tasks: Vec<serde_json::Value> = serde_json::from_str(&s).unwrap();
    for t in tasks {
        let id = t["id"].as_str().unwrap();
        let zone = t["zone"].as_str().unwrap();
        let d = t["d"].as_f64().unwrap();
        let h = t["h"].as_f64().unwrap();
        let faces_road = t["faces_road"].as_bool().unwrap_or(false);
        let skylight = t["skylight"].as_bool().unwrap_or(false);
        let engawa = t["engawa"].as_bool().unwrap_or(false);
        let norm = t["norm"].as_str().unwrap();
        let expected: f64 = norm.trim_start_matches("num:").parse().unwrap();

        let engine = daylight_coefficient(zone, d, h, faces_road, skylight, engawa);
        // 正解は小数2桁で丸められている (num:0.50 等)。±0.006 を一致とみなす。
        let ok = (engine - expected).abs() < 0.006;
        println!("{}\t{:.4}\t{:.4}\t{}", id, engine, expected, if ok { 1 } else { 0 });
    }
}
