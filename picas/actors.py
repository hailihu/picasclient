# -*- coding: utf-8 -*-
"""
@licence: The MIT License (MIT)
@Copyright (c) 2016, Jan Bot
@author: Jan Bot, Joris Borgdorff
"""

import logging
import signal
import subprocess

from .util import Timer
from .iterators import TaskViewIterator, EndlessViewIterator

from couchdb.http import ResourceConflict

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


class AbstractRunActor(object):
    """
    Executor class to be overwritten in the client implementation.
    """

    def __init__(self, db, iterator=None, view='todo', token_reset_values=[0, 0], **view_params):
        """
        @param database: the database to get the tasks from.
        @param token_reset_values: values to use in the token when PiCaS is terminated, defaults to values of 'todo' ([0,0])
        """
        if db is None:
            raise ValueError("Database must be initialized")
        self.db = db
        self.iterator = iterator
        self.token_reset_values = token_reset_values

        # current task is needed to reset it when PiCaS is killed
        self.current_task = None
        # the subprocess running the token code is necessary s.t. the handler can cleanly kill it
        self.subprocess = None
        self.tasks_processed = 0

        if iterator is None:
            self.iterator = TaskViewIterator(self.db, view, **view_params)
        else:
            self.iterator = iterator

    def _run(self, task):
        """
        Execution of the work on the iterator used in the run method.
        """
        self.prepare_run()
        # current task is set s.t. the handler can reset the task that is being worked on
        self.current_task = task

        try:
            self.process_task(task)
        except Exception as ex:
            msg = ("Exception {0} occurred during processing: {1}"
                   .format(type(ex), ex))
            task.error(msg, exception=ex)
            log.info(msg)

        while True:
            try:
                self.db.save(task)
                break
            except ResourceConflict:
                # simply overwrite changes - model results are more
                # important
                new_task = self.db.get(task.id)
                task['_rev'] = new_task.rev

        self.cleanup_run()
        self.tasks_processed += 1

    def run(self):
        """
        Run method of the actor, executes the application code by iterating
        over the available tasks in CouchDB.
        """
        # The error handler for when SLURM (or other scheduler / user) kills PiCaS, to reset the
        # token back to 'todo' state (or other state defined through the token_reset_values)
        self.setup_handler()

        self.time = Timer()
        self.prepare_env()
        try:
            for task in self.iterator:
                self._run(task)
                self.current_task = None  # set to None so the handler leaves the token alone when picas is killed
        finally:
            self.cleanup_env()

    def handler(self, signum, frame):
        """
        Signal handler method. It sets the tokens values of 'lock' and 'done' fields to the values
        passed to token_reset_values. This method ensures that when PiCaS is killed by the
        scheduler or user, it automatically resets the token that was being worked on back to some
        state (default: 'todo' state).

        @param signum: signal to listen to and act upon
        @param frame: stack frame, defaults to None, see https://docs.python.org/3/library/signal.html#signal.signal
        """
        log.info(f'PiCaS shutting down, called with signal {signum}')

        # gracefully kill the process running token code, it needs to stop before we update the token state
        if self.subprocess and self.subprocess.poll() is None:
            log.info('Terminating execution of token')
            self.subprocess.terminate()
            try:
                self.subprocess.communicate(timeout=30)  # wait 30 seconds for termination, value chosen to allow complex processes to stop
            except subprocess.TimeoutExpired:
                log.info('Killing subprocess')
                self.subprocess.kill()
                self.subprocess.communicate()

        # update the token state, if reset vaue is None, do nothing.
        if self.current_task and self.token_reset_values is not None:
            self.current_task['lock'] = self.token_reset_values[0]
            self.current_task['done'] = self.token_reset_values[1]
            self.db.save(self.current_task)

        self.cleanup_env()
        exit(0)

    def setup_handler(self):
        """
        Method to set up the handler in the run method with lower redundancy
        """
        log.info('Setting up signal handlers')
        signal.signal(signal.SIGTERM, self.handler)
        signal.signal(signal.SIGINT, self.handler)

    def prepare_env(self, *args, **kwargs):
        """
        Method to be called to prepare the environment to run the
        application.
        """

    def prepare_run(self, *args, **kwargs):
        """
        Code to run before a task gets processed. Used e.g. for fetching
        inputs.
        """

    def process_task(self, task):
        """
        The function to override, which processes the tasks themselves.
        @param task: the task to process
        """
        raise NotImplementedError

    def cleanup_run(self, *args, **kwargs):
        """
        Code to run after a task has been processed.
        """

    def cleanup_env(self, *args, **kwargs):
        """
        Method which gets called after the run method has completed.
        """

        
class RunActor(AbstractRunActor):
    """
    RunActor class with added stopping functionality.
    """

    def run(self, max_time=None, avg_time_factor=0.0, max_tasks=0, stop_function=None, **stop_function_args):
        """
        Run method of the actor, executes the application code by iterating
        over the available tasks in CouchDB, including stop logic. The stop
        logic is also extended into the EndlessViewIterator to break it when
        the condition is met, otherwise it never stops.

        @param max_time: maximum time to run picas before stopping
        @param avg_time_factor: used for estimating when to stop with `max_time`,
                                value is average time per token to run
        @param max_tasks: number of tasks that are performed before stopping
        @param stop_function: custom function to stop the execution, must return bool
        @param stop_function_args: kwargs to supply to stop_function
        """
        self.time = Timer()
        self.prepare_env()

        # handler needs to be setup in overwritten method
        self.setup_handler()

        # Special case to break the while loop of the EndlessViewIterator:
        # The while loop cant reach the stop condition in the for loop below,
        # so pass the condition into the stop mechanism of the EVI, then the
        # iterator is stopped from EVI and not the RunActorWithStop
        if isinstance(self.iterator, EndlessViewIterator):
            self.iterator.stop_callback = stop_function
            self.iterator.stop_callback_args = stop_function_args

        try:
            for task in self.iterator:
                self._run(task)

                logging.debug("Tasks executed: ", self.tasks_processed)

                if (stop_function is not None and
                    stop_function(**stop_function_args)):
                    break

                # break if number of tasks processed is max set
                if max_tasks and self.tasks_processed == max_tasks:
                    break

                if max_time is not None:
                    # for a large number of tokens the avg time will be better (due to statistics)
                    # resulting in a better estimate of whether time.elapsed + avg_time (what will
                    # be added on the next iteration) is larger than the max_time.
                    will_elapse = (self.time.elapsed() + avg_time_factor)
                    if will_elapse > max_time:
                        break
                self.current_task = None  # set to None so the handler leaves the token alone when picas is killed
        finally:
            self.cleanup_env()
