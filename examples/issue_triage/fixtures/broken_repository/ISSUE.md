# Division by zero crashes the report

When a report has no matching records, `divide(total, count)` receives `count=0`.
The expected result is `0.0`, but the current implementation raises `ZeroDivisionError`.

