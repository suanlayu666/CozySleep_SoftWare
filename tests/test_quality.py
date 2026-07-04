"""Characterization tests for build_quality — golden values captured 2026-07-04."""
from backend import quality


class TestQualityGolden:

    def test_all_normal_readings(self):
        """Comfortable dorm environment — all fields normal."""
        data = {
            "temperature": 26,
            "humidity": 61,
            "mq135_adc": 2886,
            "mq135_mv": 2329,
            "motion": 1,
            "sound_db": 48,
        }
        q = quality.build_quality(data)
        assert q["overall_level"] == "normal"
        assert q["summary"] == "宿舍环境整体正常"
        assert q["fields"]["temperature"]["text"] == "舒适"
        assert q["fields"]["temperature"]["level"] == "normal"
        assert q["fields"]["humidity"]["text"] == "可接受"
        assert q["fields"]["humidity"]["level"] == "normal"
        assert q["fields"]["mq135_adc"]["text"] == "良好"
        assert q["fields"]["mq135_adc"]["level"] == "normal"
        assert q["fields"]["motion"]["text"] == "有人"
        assert q["fields"]["sound_db"]["text"] == "正常"

    def test_critical_multiple_fields(self):
        """High humidity + bad air + loud noise + dead temp sensor -> overall critical."""
        data = {
            "temperature": None,
            "humidity": 95,
            "mq135_adc": 1200,
            "mq135_mv": 2000,
            "motion": 0,
            "sound_db": 80,
        }
        q = quality.build_quality(data)
        assert q["overall_level"] == "critical"
        assert q["fields"]["temperature"]["level"] == "missing"
        assert q["fields"]["temperature"]["text"] == "无数据"
        assert q["fields"]["humidity"]["level"] == "critical"
        assert q["fields"]["humidity"]["text"] == "过高"
        assert q["fields"]["mq135_adc"]["level"] == "critical"
        assert q["fields"]["mq135_adc"]["text"] == "较差"
        assert q["fields"]["sound_db"]["level"] == "critical"
        assert q["fields"]["sound_db"]["text"] == "噪声较大"

    def test_warning_temperature_low(self):
        """Temperature below 18 — warning."""
        q = quality.build_quality({
            "temperature": 16,
            "humidity": 50,
            "mq135_adc": 2800,
            "mq135_mv": 2200,
            "motion": 0,
            "sound_db": 40,
        })
        assert q["overall_level"] == "warning"
        assert q["fields"]["temperature"]["level"] == "warning"
        assert q["fields"]["temperature"]["text"] == "偏低"

    def test_warning_humidity_borderline(self):
        """Humidity 86-90 range — warning (偏潮)."""
        q = quality.build_quality({
            "temperature": 24,
            "humidity": 88,
            "mq135_adc": 2800,
            "mq135_mv": 2200,
            "motion": 1,
            "sound_db": 50,
        })
        assert q["fields"]["humidity"]["level"] == "warning"
        assert q["fields"]["humidity"]["text"] == "偏潮"

    def test_warning_air_quality_borderline(self):
        """MQ135 ADC 1500-2499 range — warning (一般)."""
        q = quality.build_quality({
            "temperature": 24,
            "humidity": 60,
            "mq135_adc": 1800,
            "mq135_mv": 1500,
            "motion": 0,
            "sound_db": 35,
        })
        assert q["fields"]["mq135_adc"]["level"] == "warning"
        assert q["fields"]["mq135_adc"]["text"] == "一般"

    def test_all_fields_missing(self):
        """No sensor data at all — everything missing."""
        q = quality.build_quality({})
        assert q["overall_level"] == "warning"
        for f in ("temperature", "humidity", "mq135_adc", "mq135_mv", "motion", "sound_db"):
            assert q["fields"][f]["level"] == "missing", f"{f} should be missing"

    def test_alerts_list_populated(self):
        """Alerts list contains entries for warning/critical/missing fields."""
        q = quality.build_quality({"temperature": 16, "humidity": 50,
            "mq135_adc": 2800, "mq135_mv": 2200, "motion": 0, "sound_db": 40})
        assert len(q["alerts"]) > 0
        assert isinstance(q["alerts"][0], str)
