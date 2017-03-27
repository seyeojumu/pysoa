"""
The Client provides a simple interface for calling actions on Servers.

The base Client class provides the core workflow for request sending and receiving.
Requests are passed and returned as dicts, and the format of the request depends on the
implementation.

The on_request and on_response methods provide hooks for any actions that need to be
taken after a successful request or response. These may include, for example, logging
all requests and responses or raising exceptions on error responses.
"""
import uuid
import six
from pysoa.common.types import JobResponse


class Client(object):

    def __init__(self, service_name, transport, serializer, middleware=None):
        self.service_name = service_name
        self.transport = transport
        self.serializer = serializer
        self.middleware = middleware or []
        self.request_counter = 0

    class CallActionError(Exception):
        """
        Raised by Client.call_action(s) when a job response contains one or more action errors.

        Stores a list of ActionResponse objects, and pretty-prints their errors.

        Args:
            actions: list(ActionResponse)
        """
        def __init__(self, actions=None):
            self.actions = actions or []

        def __str__(self):
            errors_string = '\n'.join(['{a.action}: {a.errors}'.format(a=a) for a in self.actions])
            return 'Error calling action(s):\n{}'.format(errors_string)

    @staticmethod
    def generate_correlation_id():
        return six.u(uuid.uuid1().hex)

    def call_actions(
        self,
        actions,
        context=None,
        switches=None,
        correlation_id=None,
        continue_on_error=False,
        control_extra=None,
    ):
        """
        Build and send a single job request with one or more actions.

        Returns a list of action responses, one for each action, or raise an exception if any action response is an
        error.

        Args:
            switches: list
            context: dict
            correlation_id: string
            continue_on_error: bool
        Returns:
            JobResponse
        """
        request = {
            'control': control_extra or {},
            'actions': actions,
        }
        request['control']['correlation_id'] = correlation_id or self.generate_correlation_id()
        request['control']['switches'] = switches or []
        request['control']['continue_on_error'] = continue_on_error
        if context:
            request['context'] = context
        request_id = self.send_request(request)
        # Dump everything from the generator. There should only be one response.
        responses = list(self.get_all_responses())
        response_id, response = responses[0]
        if response_id != request_id:
            raise Exception('Got response with ID {} for request with ID {}'.format(response_id, request_id))
        error_actions = [action for action in response.actions if action.errors]
        if error_actions:
            raise self.CallActionError(error_actions)
        return response

    def call_action(
        self,
        action_name,
        body=None,
        **kwargs
    ):
        """
        Build and send a single job request with one action.

        Returns the action response or raises an exception if the action response is an error.

        Args:
            action_name: string
            body: dict
            switches: list
            context: dict
            correlation_id: string
        Returns:
            ActionResponse
        """
        action_request = {'action': action_name}
        if body:
            action_request['body'] = body
        return self.call_actions(
            [action_request],
            **kwargs
        ).actions[0]

    def prepare_metadata(self):
        """
        Return a dict containing metadata that will be passed to
        Transport.send_request_message. Implementations should override this method to
        include any metadata required by their Transport classes.

        Returns: dict
        """
        return {'mime_type': self.serializer.mime_type}

    def send_request(self, job_request):
        """
        Serialize and send a request message, and return a request ID.

        Args:
            job_request: JobRequest dict
        Returns:
            int
        Raises:
            ConnectionError, InvalidField, MessageSendError, MessageSendTimeout,
            MessageTooLarge
        """
        request_id = self.request_counter
        self.request_counter += 1
        meta = self.prepare_metadata()
        for middleware in self.middleware:
            middleware.process_job_request(request_id, meta, job_request)
        message = self.serializer.dict_to_blob(job_request)
        self.transport.send_request_message(request_id, meta, message)
        return request_id

    def get_all_responses(self):
        """
        Receive all available responses from the trasnport as a generator.

        Yields:
            (int, JobResponse)
        Raises:
            ConnectionError, MessageReceiveError, MessageReceiveTimeout, InvalidMessage,
            StopIteration
        """
        while True:
            request_id, meta, message = self.transport.receive_response_message()
            if message is None:
                break
            else:
                raw_response = self.serializer.blob_to_dict(message)
                job_response = JobResponse(**raw_response)
                for middleware in self.middleware:
                    middleware.process_job_response(request_id, meta, job_response)
                yield request_id, job_response
