import os
import sys
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from threading import current_thread

import librosa
import numpy as np
import pydub
import soundfile as sf
import streamlit as st
import torch
from streamlit.scriptrunner.script_run_context import SCRIPT_RUN_CONTEXT_ATTR_NAME

from inference import Separator
from lib import nets, spec_utils

MODEL_PATH = "/app/models/baseline.pth"


def inference_main(
    input,
    sample_rate: int = 44100,
    n_fft: int = 2048,
    hop_length: int = 1024,
    batchsize: int = 4,
    cropsize: int = 256,
    postprocess: bool = False,
    tta: bool = False,
):
    pretrained_model = MODEL_PATH
    print("loading model...", end=" ")
    device = torch.device("cpu")
    model = nets.CascadedNet(n_fft, 32, 128)
    model.load_state_dict(torch.load(pretrained_model, map_location=device))
    # if torch.cuda.is_available() and args.gpu >= 0:
    #     device = torch.device("cuda:{}".format(args.gpu))
    #     model.to(device)
    print("done")

    print("loading wave source...", end=" ")
    X, sr = librosa.load(
        input, sample_rate, False, dtype=np.float32, res_type="kaiser_fast"
    )
    basename = os.path.splitext(os.path.basename(input))[0]
    print("done")

    if X.ndim == 1:
        # mono to stereo
        X = np.asarray([X, X])

    print("stft of wave source...", end=" ")
    X_spec = spec_utils.wave_to_spectrogram(X, hop_length, n_fft)

    print("done")

    sp = Separator(model, device, batchsize, cropsize, postprocess)

    if tta:
        y_spec, v_spec = sp.separate_tta(X_spec)
    else:
        y_spec, v_spec = sp.separate(X_spec)

    print("validating output directories...", end=" ")
    os.makedirs("/app-data/instrumentals/", exist_ok=True)
    os.makedirs("/app-data/vocals/", exist_ok=True)
    print("done")

    print("inverse stft of instruments...", end=" ")
    wave = spec_utils.spectrogram_to_wave(y_spec, hop_length=hop_length)

    print("done")
    # eventually use diretories instead of mutating song names - should make a database easier
    # mp3 conversion

    (
        instrumental_filename_wav,
        instrumental_filename,
        vocal_filename_wav,
        vocal_filename,
    ) = get_filenames(Path(basename).stem, tta, postprocess)

    sf.write(instrumental_filename_wav, wave.T, sr)

    print("converting to mp3...", end=" ")
    sound = pydub.AudioSegment.from_wav(instrumental_filename_wav)

    sound.export(instrumental_filename, format="mp3")
    print("done")
    print(f"convered to {instrumental_filename}")

    print("inverse stft of vocals...", end=" ")
    wave = spec_utils.spectrogram_to_wave(v_spec, hop_length=hop_length)
    print("done")
    # eventually use diretories instead of mutating song names - should make a database easier
    # mp3 conversion

    sf.write(vocal_filename_wav, wave.T, sr)

    print("converting to mp3...", end=" ")
    sound = pydub.AudioSegment.from_wav(vocal_filename_wav)
    sound.export(vocal_filename, format="mp3")
    print("done")

    return None


def get_filenames(basename, use_tta, use_postprocess):

    suffix = ""
    if use_tta:
        suffix += ".tta"
    if use_postprocess:
        suffix += ".postprocess"

    instrumental_filename = "/app-data/instrumentals/" + basename + f"{suffix}.mp3"
    instrumental_filename_wav = "/app-data/instrumentals/" + basename + f"{suffix}.wav"

    vocal_filename = "/app-data/vocals/" + basename + f"{suffix}.mp3"
    vocal_filename_wav = "/app-data/vocals/" + basename + f"{suffix}.wav"

    print(f"filenames: {instrumental_filename} \n {vocal_filename}")

    return (
        instrumental_filename_wav,
        instrumental_filename,
        vocal_filename_wav,
        vocal_filename,
    )


@contextmanager
def st_redirect(src, dst):
    placeholder = st.empty()
    output_func = getattr(placeholder, dst)

    with StringIO() as buffer:
        old_write = src.write

        def new_write(b):
            if getattr(current_thread(), SCRIPT_RUN_CONTEXT_ATTR_NAME, None):
                buffer.write(b)
                output_func(buffer.getvalue())
            else:
                old_write(b)

        try:
            src.write = new_write
            yield
        finally:
            src.write = old_write


@contextmanager
def st_stdout(dst):
    with st_redirect(sys.stdout, dst):
        yield


@contextmanager
def st_stderr(dst):
    with st_redirect(sys.stderr, dst):
        yield


def check_if_already_processed(instrumental_filename, vocal_filename):
    with st_stdout("code"):
        print(
            f"checking if instrumentals and vocals exist for {Path(instrumental_filename).name}"
        )
    if Path(instrumental_filename).exists() and Path(vocal_filename).exists():
        return True
    return False


def check_and_download(
    instrumental_filename,
    vocal_filename,
    n_try: int = 0,
):
    try:
        with open(instrumental_filename, "rb") as f:
            st.download_button(
                "Download Instrumentals",
                f,
                file_name=Path(instrumental_filename).stem
                + "-Instrumental"
                + Path(instrumental_filename).suffix,
            )
        with open(vocal_filename, "rb") as f:
            st.download_button(
                "Download Vocals",
                f,
                file_name=Path(vocal_filename).stem
                + "-Vocals"
                + Path(vocal_filename).suffix,
            )
    except FileNotFoundError:
        all_files = Path("/app-data").glob("*.mp3")
        all_files_str = [str(x.name) for x in all_files]
        with st_stdout("error"):
            print(f"{all_files_str}")
            if n_try < 5:
                check_and_download(instrumental_filename, vocal_filename, n_try + 1)
            else:
                raise FileNotFoundError


def main():
    # instrumental_output_mp3 = ""
    # vocal_output_mp3 = ""
    instrumental_filename_wav = ""
    instrumental_filename = ""
    vocal_filename_wav = ""
    vocal_filename = ""

    with st.form("song_form"):
        data = st.file_uploader("Upload mp3 to split instrumentals from", type=["mp3"])
        use_tta = st.checkbox(
            "TTA - option performs Test-Time-Augmentation to improve the separation quality."
        )
        use_postprocess = st.checkbox(
            "Use Postprocessing option masks instrumental part based on the vocals volume to improve the separation quality. **EXPERIMENTAL ACCORDING TO MAINTAINER OF VOAL-REMOVER REPO** "
        )

        submitted = st.form_submit_button("Punch it Chewy!")
        if submitted:

            basename = data.name
            (
                instrumental_filename_wav,
                instrumental_filename,
                vocal_filename_wav,
                vocal_filename,
            ) = get_filenames(Path(basename).stem, use_tta, use_postprocess)

            if check_if_already_processed(instrumental_filename, vocal_filename):
                with st_stdout("success"):
                    print("File already processed")
            else:

                # with st_stdout("error"):
                #     print("Still waiting...")

                with st_stdout("code"):
                    print("Reading Data\n")
                    audio_bytes = data.read()

                    # write out to file until I figure out how to just pass audio_bytes to the inference script
                    print("Saving to staging file")
                    with open(f"/app-data/{basename}", "wb") as outfile:
                        outfile.write(audio_bytes)
                    # allow listening to song through app as sanity check
                    # st.audio(audio_bytes, format="audio/mp3")
                with st_stdout("code"):
                    inference_main(
                        f"/app-data/{basename}",
                        tta=use_tta,
                        postprocess=use_postprocess,
                    )

            with open(instrumental_filename, "rb") as f:
                instrumental_output_bytes = f.read()
            # allow listening to song through app as sanity check
            st.write("Have a listen to the instrumental!")
            st.audio(instrumental_output_bytes, format="audio/mp3")

            with open(vocal_filename, "rb") as f:
                vocal_output_bytes = f.read()
            # allow listening to song through app as sanity check
            st.write("Have a listen to the vocals!")
            st.audio(vocal_output_bytes, format="audio/mp3")

    # TODO: download it...
    st.markdown("### Once the song is split you will be able to download below")
    try:
        check_and_download(instrumental_filename, vocal_filename)

    except FileNotFoundError:
        with st_stdout("error"):
            print("Still waiting...")


st.markdown("# Audacity who??")
st.markdown(
    "**Upload one mp3 at a time and the vocal-remover app will strip the instrumentals from the vocals. There are 2 options to play with and you can listen to the instrumental/vocal tracks separately from here before downloading**"
)
main()

# TODO: download it...
# st.markdown("### Once the song is split you will be able to download below")
# check_and_download(use_tta, use_postprocess, instrumental_output_mp3, vocal_output_mp3)
