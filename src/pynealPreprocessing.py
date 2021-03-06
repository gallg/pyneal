""" Preprocessing Module for Real-time Scan

Set of utilities for apply specified preprocessing steps to data during a
real-time run.

"""
import sys
import logging
import io
import contextlib

import zmq
import numpy as np
import nibabel as nib
from nipy.algorithms.registration import HistogramRegistration, Rigid


class Preprocessor:
    """ Preprocessing class.

    This is the main Preprocessing module that gets instantiated by Pyneal, and
    will handle executing specific preprocessing routines on incoming volumes
    throughout the scan.

    """
    def __init__(self, settings):
        """ Initialize the class

        Parameters
        ----------
        settings : dict
            dictionary that contains all of the pyneal settings for the current
            session. This dictionary is loaded/configured by the GUI once
            Pyneal is first launched

        """
        # set up logger
        self.logger = logging.getLogger('PynealLog')

        self.settings = settings
        self.affine = None

        # start the motion thread
        self.motionProcessor = MotionProcessor(logger=self.logger, refVolIdx=4)

        # create the socket to send data to dashboard (if dashboard there be)
        if self.settings['launchDashboard']:
            self.dashboard = True
            context = zmq.Context.instance()
            self.dashboardSocket = context.socket(zmq.REQ)
            self.dashboardSocket.connect('tcp://127.0.0.1:{}'.format(self.settings['dashboardPort']))

    def set_affine(self, affine):
        """ Set a local reference to the RAS+ affine transformation for the
        current series

        Parameters
        ----------
        affine : (4,4) numpy array-like
            affine matrix mapping the current series to RAS+ space

        """
        self.affine = affine

    def runPreprocessing(self, vol, volIdx):
        """ Run preprocessing on the supplied volume

        This is a function that Pyneal can call in order to execute the
        specified preprocessing routines for this series.

        Parameters
        ----------
        vol : numpy-array
            3D array of voxel data for the current volume
        volIdx : int
            0-based index indicating where, in time (4th dimension), the volume
            belongs

        Returns
        -------
        vol : numpy-array
            preprocessed 3D array of voxel data for the current volume

        """
        ### calculate the motion parameters on this volume. motionParams are
        # returned as dictionary with keys for 'rms_abs', and 'rms_rel';
        # NOTE: estimateMotion needs the input vol to be a nibabel nifti obj
        # the nostdout bit suppresses verbose estimation output to stdOut
        self.logger.debug('started volIdx {}'.format(volIdx))
        
        if self.settings['estimateMotion']:
            with nostdout():
                motionParams = self.motionProcessor.estimateMotion(
                    nib.Nifti1Image(vol, self.affine),
                    volIdx)

            ### send to dashboard (if specified)
            if self.settings['launchDashboard']:
                if motionParams is not None:

                    # send to the dashboard
                    self.sendToDashboard(topic='motion',
                                        content={'volIdx': volIdx,
                                                'rms_abs': motionParams['rms_abs'],
                                                'rms_rel': motionParams['rms_rel']})

        self.logger.info('preprocessed volIdx {}'.format(volIdx))
        return vol

    def sendToDashboard(self, topic=None, content=None):
        """ Send a msg to the Pyneal dashboard.

        The dashboard expects messages formatted in specific way, namely a
        dictionary with 2 keys: 'topic', and 'content'

        Parameters
        ----------
        topic : string
            topic of the message. For instance, topic = 'motion', would tell
            the dashboard to parse this message for data to use in the motion
            plot
        content : dict
            dictionary containing all of the key:value pairs you want to
            include in your message

        """
        if self.dashboard:
            dashboardMsg = {'topic': topic,
                            'content': content}
            self.dashboardSocket.send_json(dashboardMsg)
            response = self.dashboardSocket.recv_string()


class MotionProcessor():
    """ Tool to estimate motion during a real-time run.

    The motion estimates will be made relative to a reference volume,
    specifed by `refVolIdx` (0-based index), and relative to the previous
    volume.


    See Also
    --------
    Motion estimation based on:
    https://github.com/cni/rtfmri/blob/master/rtfmri/analyzers.py &
    https://www.sciencedirect.com/science/article/pii/S1053811917306729#bib32

    """
    def __init__(self, logger=None, refVolIdx=4):
        """ Initialize the class

        Parameters
        ----------
        logger : logger object, optional
            reference to the logger object where you want to write log messages
        refVolIdx : int, optional
            The index of the volume to make absolute motion estimates relative
            to. 0-based index (default: 4)

        """
        self.logger = logger
        self.refVolIdx = refVolIdx
        self.refVol = None

        # initialize
        self.refVol_T = Rigid(np.eye(4))
        self.prevVol_T = Rigid(np.eye(4))

    def estimateMotion(self, niiVol, volIdx):
        """ Estimate the motion parameters for the current volume.

        This tool will first estimate the transformation needed to align the
        current volume to the reference volume. This transformation can be
        expressed as a rigid body transformation with 6 degrees of freedom
        (translation x,y,z; rotation x,y,z).

        Using the estimated transformation matrix, we can compute RMS deviation
        as a single value representing the displacement (in mm) between the
        current volume and the reference volume (abs rms) or the current volume
        and the previous volume (relative rms).

        This approach for estimating motion borrows heavily from:
        https://github.com/cni/rtfmri/blob/master/rtfmri/analyzers.py

        RMS calculations:
        https://www.fmrib.ox.ac.uk/datasets/techrep/tr99mj1/tr99mj1.pdf

        Parameters
        ----------
        niiVol : nibabel-like image
            nibabel-like 3D data object, representing the current volume
        volIdx : int
            the 0-based index of the current volume along the 4th dim
            (i.e. time)

        """
        if volIdx < self.refVolIdx:
            return None

        elif volIdx == self.refVolIdx:
            self.refVol = niiVol            # set the reference volume
            return None

        elif volIdx > self.refVolIdx:
            # create a regisitration object
            reg = HistogramRegistration(niiVol, self.refVol, interp='tri')

            # estimate optimal transformation
            T = reg.optimize(self.prevVol_T.copy(), ftol=0.1, maxfun=30)

            # compute RMS relative to reference vol (rms abs)
            rms_abs = self.computeRMS(self.refVol_T, T)

            # compute RMS relative to previous vol (rms rel)
            rms_rel = self.computeRMS(self.prevVol_T, T)

            # # get the realignment parameters:
            # rot_x, rot_y, rot_z = np.rad2deg(T.rotation)
            # trans_x, trans_y, trans_z = T.translation

            # update the estimate
            self.prevVol_T = T

            motionParams = {'rms_abs': rms_abs,
                            'rms_rel': rms_rel}
            return motionParams

    def computeRMS(self, T1, T2, R=50):
        """ Compute the RMS displacement between transformation matrices.

        Parameters
        ----------
        T1,T2 : nipy Rigid object
            Transformation matrices
        R : int, optional
            radius (in mm) from center of head to cerebral cortex. Defaults to
            50mm (apprx distance from cerebral cortex to center of head):
            Motion-related artifacts in structural brain images revealed with
            independent estimates of in-scanner head motion. (2017) Savalia,
            et al. Human Brain Mapping. Jan; 38(1)
            https://www.ncbi.nlm.nih.gov/pubmed/27634551

        Returns
        -------
        rms : float
            a single value representing the mean displacement in mm (assuming
            a spherical volume with radius, R).

        See Also
        --------
        This approach for estimating motion borrows heavily from:
        https://github.com/cni/rtfmri/blob/master/rtfmri/analyzers.py

        RMS calculations:
        https://www.fmrib.ox.ac.uk/datasets/techrep/tr99mj1/tr99mj1.pdf

        """
        diffMatrix = T1.as_affine().dot(np.linalg.inv(T2.as_affine())) - np.eye(4)

        # decompose into A and t components
        A = diffMatrix[:3, :3]
        t = diffMatrix[:3, 3]

        # volume center assumed to be at 0,0,0 in world space coords
        center = np.zeros(3)
        t += A.dot(center)

        # compute RMS error (aka deviation error between transforms)
        rms = np.sqrt((1 / 5) * R**2 * A.T.dot(A).trace() + t.T.dot(t))

        return rms


# suppress stdOut from verbose functions
@contextlib.contextmanager
def nostdout():
    save_stdout = sys.stdout
    sys.stdout = io.StringIO()
    yield
    sys.stdout = save_stdout
