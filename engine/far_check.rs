#[allow(dead_code)]
#[path = "../../mu-house/crates/houki-engine/src/lib.rs"]
mod houki_engine;

use bim_core::{Element, SiteContext, ZoningRuleOverride};
use houki_engine::check_far;
use std::io::Read;

fn main() {
    let mut s = String::new();
    std::io::stdin().read_to_string(&mut s).unwrap();
    let tasks: Vec<serde_json::Value> = serde_json::from_str(&s).unwrap();
    for t in tasks {
        let id = t["id"].as_str().unwrap();
        let zone = t["zone"].as_str().unwrap();
        let road = t["road"].as_f64().unwrap();
        let planned = t["planned"].as_f64().unwrap();
        let norm = t["norm"].as_str().unwrap();
        let expected: f64 = norm.trim_start_matches("frac:").parse().unwrap();

        let lim = houki_engine::zoning_limits(zone);
        let site = SiteContext {
            zoning: zone.to_string(),
            land_area_m2: 100.0,
            road_width_m: road,
            road_width_known: true,
            rule_override: Some(ZoningRuleOverride {
                coverage_pct_max: lim.coverage_pct_max,
                far_pct_max: planned * 100.0,
                road_slope_n: lim.road_slope.0,
                road_slope_d: lim.road_slope.1,
                road_applicable_distance_m: lim.road_applicable_distance_m,
                north_slope_start_mm: lim.north_slope_start_mm,
                absolute_height_limit_mm: lim.absolute_height_limit_mm,
                far_road_coefficient: houki_engine::far_road_coefficient(zone),
            }),
            ..Default::default()
        };
        // 1 floor slab of 100 m² → far_pct = 100/100 = 100% ; limit_pct はエンジンの答え
        let el = Element {
            id: "s".into(), cls: "IFCSLAB".into(), label: "1F".into(), descr: String::new(),
            shape: "box".into(), x: 0, y: 0, z: 0, w: 10000, d: 10000, h: 100, rotation: 0.0,
        };
        let rep = check_far(&[el], &site);
        let engine = rep.limit_pct / 100.0; // 倍率に正規化
        let ok = (engine - expected).abs() < 0.005;
        println!("{}\t{:.4}\t{:.4}\t{}", id, engine, expected, if ok { 1 } else { 0 });
    }
}
