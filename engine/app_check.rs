#[allow(dead_code)]
#[path = "../../mu-house/crates/houki-engine/src/lib.rs"]
mod houki_engine;

use bim_core::{Element, SiteContext, ZoningRuleOverride};
use houki_engine::{check_coverage, check_far};
use std::io::Read;

fn main() {
    let mut s = String::new();
    std::io::stdin().read_to_string(&mut s).unwrap();
    let tasks: Vec<serde_json::Value> = serde_json::from_str(&s).unwrap();
    for t in tasks {
        let id = t["id"].as_str().unwrap();
        let zone = t["zone"].as_str().unwrap();
        let site_area = t["site"].as_f64().unwrap();
        let planned_far = t["planned_far"].as_f64().unwrap();
        let planned_bcr = t["planned_bcr"].as_f64().unwrap();
        let road = t["road"].as_f64().unwrap();
        let norm = t["norm"].as_str().unwrap();
        let parts: Vec<&str> = norm.trim_start_matches("applied#").split('#').collect();
        let exp_far: f64 = parts[0].parse().unwrap();
        let exp_bcr: f64 = parts[1].parse().unwrap();

        let lim = houki_engine::zoning_limits(zone);
        let site = SiteContext {
            zoning: zone.to_string(),
            land_area_m2: site_area,
            road_width_m: road,
            road_width_known: true,
            rule_override: Some(ZoningRuleOverride {
                coverage_pct_max: planned_bcr * 100.0,
                far_pct_max: planned_far * 100.0,
                road_slope_n: lim.road_slope.0,
                road_slope_d: lim.road_slope.1,
                road_applicable_distance_m: lim.road_applicable_distance_m,
                north_slope_start_mm: lim.north_slope_start_mm,
                absolute_height_limit_mm: lim.absolute_height_limit_mm,
                far_road_coefficient: houki_engine::far_road_coefficient(zone),
            }),
            ..Default::default()
        };
        let el = Element {
            id: "s".into(), cls: "IFCSLAB".into(), label: "1F".into(), descr: String::new(),
            shape: "box".into(), x: 0, y: 0, z: 0, w: 10000, d: 10000, h: 100, rotation: 0.0,
        };
        let far_rep = check_far(&[el.clone()], &site);
        let bcr_rep = check_coverage(&[el], &site);
        // 延べ面積上限 = 敷地 × 容積率限度, 建築面積上限 = 敷地 × 建蔽率限度
        let eng_far = site_area * far_rep.limit_pct / 100.0;
        let eng_bcr = site_area * bcr_rep.limit_pct / 100.0;
        let ok = (eng_far - exp_far).abs() < 0.5 && (eng_bcr - exp_bcr).abs() < 0.5;
        println!("{}\t{:.1}\t{:.1}\t{:.1}\t{:.1}\t{}", id, eng_far, exp_far, eng_bcr, exp_bcr, if ok { 1 } else { 0 });
    }
}
