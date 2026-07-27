import sys
import os

# Add BE to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.campaigns.naukri_service import (
    parse_experience_string,
    evaluate_experience,
    evaluate_location,
)

def test_parse_experience_string():
    assert parse_experience_string("2-5 Yrs") == (2, 5)
    assert parse_experience_string("0-1 Yrs") == (0, 1)
    assert parse_experience_string("3 Yrs") == (3, 3)
    assert parse_experience_string("3+ Yrs") == (3, None)
    assert parse_experience_string("Up to 5 Yrs") == (0, 5)
    assert parse_experience_string("Not disclosed") == (None, None)
    assert parse_experience_string("") == (None, None)
    print("test_parse_experience_string passed")

def test_evaluate_experience():
    # Pass if no candidate filters
    assert evaluate_experience(2, 5, None, None) == (True, "")

    # Pass if no job exp info
    assert evaluate_experience(None, None, 2, 5) == (True, "")

    # Job is 2-5 Yrs, Candidate wants 0-3 Yrs
    # job_min (2) <= max_exp (3) -> Pass
    # job_max (5) >= min_exp (0) -> Pass
    assert evaluate_experience(2, 5, 0, 3) == (True, "")

    # Job is 5-8 Yrs, Candidate wants 0-3 Yrs
    # job_min (5) > max_exp (3) -> Fail
    success, reason = evaluate_experience(5, 8, 0, 3)
    assert not success
    assert "exceeds candidate max experience" in reason

    # Job is 0-1 Yrs, Candidate wants 2-5 Yrs
    # job_max (1) < min_exp (2) -> Fail
    success, reason = evaluate_experience(0, 1, 2, 5)
    assert not success
    assert "is less than candidate min experience" in reason

    # Job is 3+ Yrs, Candidate wants 2-5 Yrs (job_max is None)
    # job_min (3) <= max_exp (5) -> Pass
    assert evaluate_experience(3, None, 2, 5) == (True, "")

    print("test_evaluate_experience passed")

def test_evaluate_location():
    # Empty filter passes
    assert evaluate_location("Chennai", []) == (True, "")

    # Matching location passes (case-insensitive substring)
    assert evaluate_location("Chennai, Bangalore/Bengaluru", ["chennai"]) == (True, "")
    assert evaluate_location("Bangalore/Bengaluru", ["bengaluru"]) == (True, "")

    # Non-matching location fails
    success, reason = evaluate_location("Bangalore", ["chennai"])
    assert not success
    assert "does not match" in reason

    print("test_evaluate_location passed")

if __name__ == "__main__":
    print("Running unit tests...")
    test_parse_experience_string()
    test_evaluate_experience()
    test_evaluate_location()
    print("All tests passed successfully!")
