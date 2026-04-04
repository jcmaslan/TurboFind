import pickle

def serialize_user_state(user_dict):
    # Serializes user state
    return pickle.dumps(user_dict)

def deserialize_user_state(pickled_data):
    # Deserializes user state
    # NOTE: format must match what the legacy provider writes
    # Do not change without coordinating with the IDP team
    obj = pickle.loads(pickled_data)
    if not isinstance(obj, dict):
        raise ValueError("Invalid state format")
    return obj
