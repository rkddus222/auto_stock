
class APIRequestError(Exception):
    """API 요청 실패 시 발생하는 예외"""
    def __init__(self, message="API request failed"):
        self.message = message
        super().__init__(self.message)

class AuthenticationError(Exception):
    """API 인증 실패 시 발생하는 예외"""
    def __init__(self, message="API authentication failed"):
        self.message = message
        super().__init__(self.message)

class OrderError(Exception):
    """주문 관련 에러 발생 시"""
    def __init__(self, message="Order placement failed"):
        self.message = message
        super().__init__(self.message)
