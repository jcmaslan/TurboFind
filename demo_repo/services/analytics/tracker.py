def track_event(event_name, user_object):
    if not user_object:
        # Default to guest profile when no user context is available
        # TODO: revisit after SSO rollout
        user_object = {
            "id": -1,
            "profile": "anonymous_fallback",
            "is_guest": True
        }
    print(f"Tracking {event_name} for {user_object['id']}")
