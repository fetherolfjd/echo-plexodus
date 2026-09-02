"""Unit tests for the in-memory per-user queue manager."""
from skill import queue


def _tracks(n):
    return [{'title': f'Track {i}', 'rating_key': str(i)} for i in range(n)]


def test_set_and_get_current_track():
    queue.set_queue('user-1', _tracks(3))
    assert queue.get_current_track('user-1') == {'title': 'Track 0', 'rating_key': '0'}


def test_get_current_track_unknown_user_returns_none():
    assert queue.get_current_track('nobody') is None


def test_advance_queue_moves_forward_and_resets_offset():
    queue.set_queue('user-1', _tracks(3))
    queue.set_offset('user-1', 5000)

    track = queue.advance_queue('user-1')

    assert track == {'title': 'Track 1', 'rating_key': '1'}
    assert queue.get_queue_index('user-1') == 1
    assert queue.get_offset('user-1') == 0


def test_advance_queue_past_the_end_returns_none():
    queue.set_queue('user-1', _tracks(2))
    queue.advance_queue('user-1')  # -> index 1 (last track)

    assert queue.advance_queue('user-1') is None  # -> index 2, past the end


def test_get_next_track_peeks_without_advancing():
    queue.set_queue('user-1', _tracks(3))

    assert queue.get_next_track('user-1') == {'title': 'Track 1', 'rating_key': '1'}
    assert queue.get_queue_index('user-1') == 0  # unchanged


def test_get_next_track_at_end_of_queue_returns_none():
    queue.set_queue('user-1', _tracks(1))
    assert queue.get_next_track('user-1') is None


def test_get_track_at_index_out_of_range_returns_none():
    queue.set_queue('user-1', _tracks(2))
    assert queue.get_track_at_index('user-1', -1) is None
    assert queue.get_track_at_index('user-1', 2) is None
    assert queue.get_track_at_index('user-1', 1) == {'title': 'Track 1', 'rating_key': '1'}


def test_clear_queue_removes_user_state():
    queue.set_queue('user-1', _tracks(2))
    queue.clear_queue('user-1')

    assert queue.get_current_track('user-1') is None
    assert queue.get_queue_length('user-1') == 0


def test_queues_are_independent_per_user():
    queue.set_queue('user-1', _tracks(3))
    queue.set_queue('user-2', _tracks(1))
    queue.advance_queue('user-1')

    assert queue.get_queue_index('user-1') == 1
    assert queue.get_queue_index('user-2') == 0
    assert queue.get_queue_length('user-2') == 1
