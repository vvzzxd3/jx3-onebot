"""Provider diagnostics preserve complete synthetic code values in logs."""
import unittest

from jx3_bot.errors import ProviderError
from jx3_bot.observability import Observer


class ErrorObservationTests(unittest.TestCase):
    def test_provider_error_code_is_not_cut_before_logging(self):
        code = "token=SYNTHETIC " + "错误" * 20000 + " END_OF_CODE"
        error = ProviderError("weather", 200, code)
        observer = Observer(known_secrets=("SYNTHETIC",))
        self.assertEqual(error.code, code)
        self.assertEqual(observer.code(error.code), code)
        with self.assertLogs("jx3_bot.observability", level="INFO") as captured:
            observer.event("weather.error", code=observer.code(error.code))
        self.assertIn(code, captured.output[0])


if __name__ == "__main__":
    unittest.main()
