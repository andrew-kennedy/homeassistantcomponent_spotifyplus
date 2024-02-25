"""Support for interacting with Spotify Connect."""
from __future__ import annotations

import datetime as dt
from datetime import timedelta, datetime
import time
from typing import Any, Callable, Concatenate, ParamSpec, TypeVar, Tuple
from yarl import URL

import spotipy
from spotifywebapipython import SpotifyClient, SpotifyApiError, SpotifyWebApiError
from spotifywebapipython.models import (
    Album,
    AlbumPageSaved,
    AlbumPageSimplified,
    AlbumSaved,
    AlbumSimplified,
    Artist,
    ArtistPage,
    Category,
    CategoryPage,
    Context, 
    Device, 
    Episode, 
    EpisodePageSimplified,
    PlayerPlayState, 
    PlayHistoryPage,
    Playlist, 
    PlaylistPageSimplified, 
    PlaylistSimplified,
    SearchResponse, 
    Show,
    ShowPageSaved,
    ShowSaved,
    Track,
    TrackPage,
    TrackPageSaved,
    TrackSaved,
    UserProfile
)

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    ATTR_MEDIA_ENQUEUE,
    BrowseMedia,
    MediaPlayerEnqueue,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ID
from homeassistant.core import HomeAssistant, callback, Service
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.dt import utcnow

from .appmessages import STAppMessages
from .browse_media import (
    async_browse_media_library_index, 
    BrowsableMedia,
    browse_media_node, 
    PLAYABLE_MEDIA_TYPES,
    SPOTIFY_LIBRARY_MAP
)
from .instancedata_spotifyplus import InstanceDataSpotifyPlus
from .const import (
    DOMAIN, 
    LOGGER,
)

# get smartinspect logger reference; create a new session for this module name.
from smartinspectpython.siauto import SIAuto, SILevel, SISession, SIColors, SIMethodParmListContext
import logging
_logsi:SISession = SIAuto.Si.GetSession(__name__)
if (_logsi == None):
    _logsi = SIAuto.Si.AddSession(__name__, True)
_logsi.SystemLogger = LOGGER


# annotate the `spotify_exception_handler` callable.
_SpotifyMediaPlayerT = TypeVar("_SpotifyMediaPlayerT", bound="SpotifyMediaPlayer")
_R = TypeVar("_R")
_P = ParamSpec("_P")


#SCAN_INTERVAL = timedelta(seconds=15)
SCAN_INTERVAL = timedelta(seconds=30)
""" Time interval (in seconds) to scan for player status updates. """


REPEAT_MODE_MAPPING_TO_HA = {
    "context": RepeatMode.ALL,
    "off": RepeatMode.OFF,
    "track": RepeatMode.ONE,
}
""" maps spotify repeat modes to home assistant repeat modes. """


REPEAT_MODE_MAPPING_TO_SPOTIFY = {
    value: key for key, value in REPEAT_MODE_MAPPING_TO_HA.items()
}
""" maps home assistant repeat modes to spotify repeat modes. """


async def async_setup_entry(hass:HomeAssistant, entry:ConfigEntry, async_add_entities:AddEntitiesCallback) -> None:
    """
    Set up the media player based on a config entry.

    Args:
        hass (HomeAssistant):
            HomeAssistant instance.
        entry (ConfigEntry):
            HomeAssistant configuration entry dictionary.  This contains configuration
            settings for the specific component device entry.
        async_add_entities (AddEntitiesCallback):
            Callback function to add all entities to Home Assistant for this platform.
    
    This function is called as part of the __init__.async_setup_entry event flow,
    which was initiated via the `hass.config_entries.async_forward_entry_setup` call.
    """
    try:

        # trace.
        _logsi.EnterMethod(SILevel.Debug)
        _logsi.LogObject(SILevel.Verbose, "'%s': MediaPlayer async_setup_entry is starting - entry (ConfigEntry) object" % entry.title, entry)

        # get integration instance data from HA datastore.
        data:InstanceDataSpotifyPlus = hass.data[DOMAIN][entry.entry_id]

        # create the platform instance, passing our initialization parameters.
        _logsi.LogVerbose("'%s': MediaPlayer async_setup_entry is creating the SpotifyMediaPlayer instance" % entry.title)
        media_player = SpotifyMediaPlayer(data)

        # add all entities to Home Assistant.
        _logsi.LogVerbose("'%s': MediaPlayer async_setup_entry is adding SpotifyMediaPlayer instance entities to Home Assistant" % entry.title)
        async_add_entities([media_player], True)

        # store the reference to the media player object.
        _logsi.LogVerbose("'%s': MediaPlayer async_setup_entry is storing the SpotifyMediaPlayer reference to hass.data[DOMAIN]" % entry.title)
        hass.data[DOMAIN][entry.entry_id].media_player = media_player

        _logsi.LogVerbose("'%s': MediaPlayer async_setup_entry complete" % entry.title)

    except Exception as ex:
        
        # trace.
        _logsi.LogException("'%s': MediaPlayer async_setup_entry exception" % entry.title, ex, logToSystemLogger=False)
        raise

    finally:

        # trace.
        _logsi.LeaveMethod(SILevel.Debug)


def spotify_exception_handler(
        func: Callable[Concatenate[_SpotifyMediaPlayerT, _P], _R]
        ) -> Callable[Concatenate[_SpotifyMediaPlayerT, _P], _R | None]:
    """
    Decorate SpotifyClient calls to handle Spotify exception.

    A decorator that wraps the passed in function, catches Spotify errors,
    aiohttp exceptions and handles the availability of the media player.
    """

    def wrapper(self: _SpotifyMediaPlayerT, *args: _P.args, **kwargs: _P.kwargs) -> _R | None:
        
        try:

            # call the function.
            result = func(self, *args, **kwargs)
            
            # if no exception, then assume it was successful.
            self._attr_available = True

            # give spotify some time to process the command before the update check.
            time.sleep(0.25)
            
            # inform Home Assistant of status updates.
            _logsi.LogVerbose('Calling async_write_ha_state to inform HA of any updates')
            self.async_write_ha_state()
            
            # return function result to caller.
            return result

        except SpotifyWebApiError as ex:
            
            self._attr_available = False
            _logsi.LogException(None, ex)
            raise HomeAssistantError(ex.Message) from ex
        
        except Exception as ex:

            self._attr_available = False
            _logsi.LogException(None, ex)
            raise HomeAssistantError(str(ex)) from ex

    return wrapper


class SpotifyMediaPlayer(MediaPlayerEntity):
    """
    Representation of a SpotifyPlus media player device.
    """

    def __init__(self, data:InstanceDataSpotifyPlus) -> None:
        """
        Initializes a new instance of the SpotifyPlus media player entity class.
        
        Args:
            data (InstanceDataSpotifyPlus):
                The media player entity instance data parameters that were created
                in the `__init__.async_setup_entry` method.
        """
        methodParms:SIMethodParmListContext = None
        
        try:

            # trace.
            methodParms = _logsi.EnterMethodParmList(SILevel.Debug)
            methodParms.AppendKeyValue("data.devices", str(data.devices))
            methodParms.AppendKeyValue("data.media_player", str(data.media_player))
            methodParms.AppendKeyValue("data.session", str(data.session))
            methodParms.AppendKeyValue("data.spotifyClient", str(data.spotifyClient))
            _logsi.LogMethodParmList(SILevel.Verbose, "SpotifyMediaPlayer initialization arguments", methodParms)

            # initialize instance storage.
            self._id = data.spotifyClient.UserProfile.Id
            self._nowPlaying:PlayerPlayState = None
            self._playlist:Playlist = None
            self.data = data

            # initialize base class attributes (MediaPlayerEntity).
            self._attr_icon = "mdi:spotify"
            self._attr_media_image_remotely_accessible = False
            self._attr_state = MediaPlayerState.IDLE
            
            # A unique_id for this entity within this domain.
            # Note: This is NOT used to generate the user visible Entity ID used in automations.
            self._attr_unique_id = data.spotifyClient.UserProfile.Id

            # we will set "self._attr_has_entity_name = False", which causes the "self._attr_name"
            # to be used as-is.  use "self._attr_has_entity_name = True", to append the "self._attr_name"
            # value to the end of "DeviceInfo.name" value.
            self._attr_has_entity_name = False
            self._attr_name = f"SpotifyPlus {data.spotifyClient.UserProfile.DisplayName}"
            
            # set device information.
            # this contains information about the device that is partially visible in the UI.
            # for more information see: https://developers.home-assistant.io/docs/device_registry_index/#device-properties
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, data.spotifyClient.UserProfile.Id)},
                manufacturer="Spotify AB",
                model=f"Spotify {self.data.spotifyClient.UserProfile.Product.capitalize()} Account",
                name=self._attr_name,
                entry_type=DeviceEntryType.SERVICE,
                configuration_url="https://open.spotify.com",
            )
            _logsi.LogDictionary(SILevel.Verbose, "'%s': MediaPlayer device information dictionary" % self.name, self._attr_device_info, prettyPrint=True)

            # set features supported by this media player.
            # most features will NOT be supported if the Spotify user does not have a PREMIUM
            # membership level, as the Spotify Web API requires a premium membership for most 
            # of the player control functions.
            if self.data.spotifyClient.UserProfile.Product == "premium":
                _logsi.LogVerbose("'%s': MediaPlayer is setting supported features for Spotify Premium user" % self.name)
                self._attr_supported_features = MediaPlayerEntityFeature.BROWSE_MEDIA \
                                              | MediaPlayerEntityFeature.NEXT_TRACK \
                                              | MediaPlayerEntityFeature.PAUSE \
                                              | MediaPlayerEntityFeature.PLAY \
                                              | MediaPlayerEntityFeature.PLAY_MEDIA \
                                              | MediaPlayerEntityFeature.PREVIOUS_TRACK \
                                              | MediaPlayerEntityFeature.REPEAT_SET \
                                              | MediaPlayerEntityFeature.SEEK \
                                              | MediaPlayerEntityFeature.SELECT_SOURCE \
                                              | MediaPlayerEntityFeature.SHUFFLE_SET \
                                              | MediaPlayerEntityFeature.VOLUME_SET
            else:
                _logsi.LogVerbose("'%s': MediaPlayer is setting supported features for Spotify Non-Premium user" % self.name)
                self._attr_supported_features = MediaPlayerEntityFeature.BROWSE_MEDIA

            # we will (by default) set polling to true, as the SpotifyClient does not support websockets
            # for player update notifications.
            _logsi.LogVerbose("'%s': MediaPlayer device polling is being enabled, as the device does not support websockets" % self.name)
            self._attr_should_poll = True
        
            # load option: source_list - list of supported sources.
            # in case we need it in the future ...
            # self._attr_source_list = data.options.get(CONF_OPTION_SOURCE_LIST, None)
            # _logsi.LogArray(SILevel.Verbose, "'%s': MediaPlayer configuration option: '%s' = '%s'" % (self.unique_id, CONF_OPTION_SOURCE_LIST, str(self._attr_source_list)), self._attr_source_list)

            # trace.
            _logsi.LogObject(SILevel.Verbose, "'%s': MediaPlayer SpotifyClient object" % self.name, self.data.spotifyClient)
            _logsi.LogObject(SILevel.Verbose, "'%s': MediaPlayer initialization complete" % self.name, self)

        except Exception as ex:
        
            # trace.
            _logsi.LogException("'%s': MediaPlayer initialization exception" % self.name, ex, logToSystemLogger=False)
            raise

        finally:

            # trace.
            _logsi.LeaveMethod(SILevel.Debug)


    @property
    def state(self) -> MediaPlayerState:
        """ Return the playback state. """
        return self._attr_state


    @property
    def volume_level(self) -> float | None:
        """ Volume level of the media player (0.0 to 1.0). """
        return self._attr_volume_level


    @property
    def media_content_id(self) -> str | None:
        """ Return the media URL. """
        if self._nowPlaying is not None and self._nowPlaying.Item is not None:
            return self._nowPlaying.Item.Uri
        return None


    @property
    def media_content_type(self) -> str | None:
        """ Return the media type. """
        if self._nowPlaying is not None and self._nowPlaying.Item is not None:
            if self._nowPlaying.Item.Type == MediaType.EPISODE.value:
                return MediaType.PODCAST 
            return MediaType.MUSIC
        return None


    @property
    def media_duration(self) -> int | None:
        """ Duration of current playing media in seconds. """
        return self._attr_media_duration


    @property
    def media_position(self) -> int | None:
        """ Position of current playing media in seconds. """
        return self._attr_media_position


    @property
    def media_position_updated_at(self) -> dt.datetime | None:
        """ 
        When was the position of the current playing media valid.
        
        Returns value from homeassistant.util.dt.utcnow().
        """
        return self._attr_media_position_updated_at


    @property
    def media_image_url(self) -> str | None:
        """ Return the media image URL. """
        if self._nowPlaying is not None and self._nowPlaying.Item is not None:
            
            item = self._nowPlaying.Item
        
            # for episodes, use the episode image if there is one;
            # otherwise, use the show image if there is one.
            if item.Type == MediaType.EPISODE.value:
                episode:Episode = item
                if episode.ImageUrl is not None:
                    return episode.ImageUrl
                if episode.Show.ImageUrl is not None:
                    return episode.Show.ImageUrl
                return None

            # for everything else, use the album image if there is one.
            if item.Album.ImageUrl is not None:
                return item.Album.ImageUrl
        
        return None
        

    @property
    def media_title(self) -> str | None:
        """ Return the media title. """
        if self._nowPlaying is not None and self._nowPlaying.Item is not None:
            return self._nowPlaying.Item.Name
        return None


    @property
    def media_artist(self) -> str | None:
        """ Return the media artist. """
        if self._nowPlaying is not None and self._nowPlaying.Item is not None:
            item = self._nowPlaying.Item
            if item.Type == MediaType.EPISODE.value:
                return item.Show.Publisher

            return ", ".join(artist.Name for artist in item.Artists)
        return None


    @property
    def media_album_name(self) -> str | None:
        """ Return the media album. """
        if self._nowPlaying is not None and self._nowPlaying.Item is not None:
            item = self._nowPlaying.Item
            if item.Type == MediaType.EPISODE.value:
                return item.Show.Name
            return item.Album.Name
        return None


    @property
    def media_track(self) -> int | None:
        """ Track number of current playing media, music track only. """
        if self._nowPlaying is not None and self._nowPlaying.Item is not None:
            item = self._nowPlaying.Item
            if item.Type == MediaType.TRACK.value:
                # TrackNumber will not exist for an episode.
                return self._nowPlaying.Item.TrackNumber
        return None


    @property
    def media_playlist(self):
        """ Title of Playlist currently playing. """
        if self._playlist is not None:
            return self._playlist.Name
        return None


    @property
    def source(self) -> str | None:
        """ Return the current playback device. """
        if self._nowPlaying is not None and self._nowPlaying.Device is not None:
            return self._nowPlaying.Device.Name
        return None


    @property
    def source_list(self) -> list[str] | None:
        """ Return a list of source devices. """
        deviceNames:list[str] = []
        for device in self.data.devices.data:
            deviceNames.append(device.Name)
        return deviceNames


    @property
    def shuffle(self) -> bool | None:
        """Shuffling state."""
        if self._nowPlaying is not None:
            return self._nowPlaying.ShuffleState
        return None


    @property
    def repeat(self) -> RepeatMode | str | None:
        """ Return current repeat mode. """
        return self._attr_repeat


    @spotify_exception_handler
    def set_volume_level(self, volume: float) -> None:
        """ Set the volume level. """
        self.data.spotifyClient.PlayerSetVolume(int(volume * 100))


    @spotify_exception_handler
    def media_play(self) -> None:
        """ Start or resume playback. """
        self.data.spotifyClient.PlayerMediaResume()
        self._nowPlaying._IsPlaying = True


    @spotify_exception_handler
    def media_pause(self) -> None:
        """ Pause playback. """
        self.data.spotifyClient.PlayerMediaPause()
        self._nowPlaying._IsPlaying = False


    @spotify_exception_handler
    def media_previous_track(self) -> None:
        """ Skip to previous track. """
        self.data.spotifyClient.PlayerMediaSkipPrevious()


    @spotify_exception_handler
    def media_next_track(self) -> None:
        """ Skip to next track. """
        self.data.spotifyClient.PlayerMediaSkipNext()


    @spotify_exception_handler
    def media_seek(self, position: float) -> None:
        """ Send seek command. """
        self.data.spotifyClient.PlayerMediaSeek(int(position * 1000))


    @spotify_exception_handler
    def play_media(self, media_type: MediaType | str, media_id: str, **kwargs: Any) -> None:        

        """ Play media. """
        methodParms:SIMethodParmListContext = None
        
        try:

            # trace.
            methodParms = _logsi.EnterMethodParmList(SILevel.Debug)
            methodParms.AppendKeyValue("media_type", media_type)
            methodParms.AppendKeyValue("media_id", media_id)
            methodParms.AppendKeyValue("**kwargs", kwargs)
            _logsi.LogMethodParmList(SILevel.Verbose, "SpotifyMediaPlayer Play Media arguments", methodParms)

            # remove prefix from media type.
            #media_type = media_type.removeprefix(MEDIA_PLAYER_PREFIX)

            # get enqueue keyword arguments (if any).
            enqueue: MediaPlayerEnqueue = kwargs.get(
                ATTR_MEDIA_ENQUEUE, MediaPlayerEnqueue.REPLACE
            )
        
            # resolve the device id.
            # if now playing media does not have a device id set, then use the first device
            # found in the device list (courtesy of the data update coordinator).
            deviceId:str = None
            if (self._nowPlaying is not None and self._nowPlaying.Device is None and self.data.devices.data):
                _logsi.LogVerbose("NowPlaying media has no device set - using first device in the available list of devices")
                device:Device = self.data.devices.data[0]
                deviceId = device.Id

            # is this an enqueue add request?
            if enqueue == MediaPlayerEnqueue.ADD:
                if media_type not in {
                    MediaType.TRACK,
                    MediaType.EPISODE,
                    MediaType.MUSIC,
                }:
                    raise ValueError(f"Media type {media_type} is not supported when enqueue is ADD")
                
                _logsi.LogVerbose("Adding uri to player queue: uri='%s', deviceId='%s'" % (media_id, deviceId))
                self.data.spotifyClient.AddPlayerQueueItem(media_id, deviceId)
                return
       
            # spotify can't handle URI's with query strings or anchors
            # yet, they do generate those types of URI in their official clients.
            media_id:str = str(URL(media_id).with_query(None).with_fragment(None))

            # start play based upon the media type.
            if media_type in {MediaType.TRACK, MediaType.EPISODE, MediaType.MUSIC}:
                
                _logsi.LogVerbose("Playing via PlayerMediaPlayTracks: uris='%s', deviceId='%s'" % (media_id, deviceId))
                self.data.spotifyClient.PlayerMediaPlayTracks([media_id], deviceId=deviceId)
                self._nowPlaying._IsPlaying = True
                
            elif media_type in PLAYABLE_MEDIA_TYPES:
                
                _logsi.LogVerbose("Playing via PlayerMediaPlayContext: contextUri='%s', deviceId='%s'" % (media_id, deviceId))
                self.data.spotifyClient.PlayerMediaPlayContext(media_id, deviceId=deviceId)
                self._nowPlaying._IsPlaying = True
                
            else:
                
                _logsi.LogWarning("Media type '%s' is not supported" % media_type)
                return

        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug)


    @spotify_exception_handler
    def select_source(self, source: str) -> None:
        """ Select playback device. """
        for device in self.data.devices.data:
            if device.Name == source:
                self.data.spotifyClient.PlayerTransferPlayback(device.Id, (self.state == MediaPlayerState.PLAYING))
                return


    @spotify_exception_handler
    def set_shuffle(self, shuffle: bool) -> None:
        """ Enable/Disable shuffle mode. """
        self.data.spotifyClient.PlayerSetShuffleMode(shuffle)


    @spotify_exception_handler
    def set_repeat(self, repeat: RepeatMode) -> None:
        """ Set repeat mode. """
        if repeat not in REPEAT_MODE_MAPPING_TO_SPOTIFY:
            raise ValueError(f"Unsupported repeat mode: {repeat}")
        self.data.spotifyClient.PlayerSetRepeatMode(REPEAT_MODE_MAPPING_TO_SPOTIFY[repeat])


    def update(self) -> None:
        """ Update state and attributes. """
        try:

            # trace.
            _logsi.EnterMethod(SILevel.Debug)
        
            if not self.enabled:
                _logsi.LogVerbose("Integration is disabled - nothing to update")
                return

            # # TEST TODO - force token expire!!!
            # _logsi.LogWarning("TEST TODO - Forcing token expiration in 60 seconds for testing purposes", colorValue=SIColors.Red)
            # self.data.spotifyClient.AuthToken._ExpiresIn = 30
            # unix_epoch = datetime(1970, 1, 1)
            # dtUtcNow:datetime = datetime.utcnow()
            # self.data.spotifyClient.AuthToken._ExpireDateTimeUtc = dtUtcNow + timedelta(seconds=self.data.spotifyClient.AuthToken._ExpiresIn)
            # self.data.spotifyClient.AuthToken._ExpiresAt = int((dtUtcNow - unix_epoch).total_seconds())  # seconds from epoch, current date
            # self.data.spotifyClient.AuthToken._ExpiresAt = self.data.spotifyClient.AuthToken._ExpiresAt + self.data.spotifyClient.AuthToken._ExpiresIn             # add ExpiresIn seconds

            # get now playing status.
            _logsi.LogVerbose("'%s': update method - getting nowPlaying status" % self.name)
            self._nowPlaying = self.data.spotifyClient.GetPlayerPlaybackState(additionalTypes=MediaType.EPISODE.value)
            self._UpdateNowPlayingData(self._nowPlaying)

            # did the now playing context change?
            context:Context = self._nowPlaying.Context
            if context is not None and (self._playlist is None or self._playlist.Uri != context.Uri):
                
                # yes - if it's a playlist, then we need to update the stored playlist reference.
                self._playlist = None
                if context.Type == MediaType.PLAYLIST:
                
                    try:
                        
                        _logsi.LogVerbose("Retrieving playlist for context uri '%s'" % context.Uri)
                        spotifyId:str = SpotifyClient.GetIdFromUri(context.Uri)
                        self._playlist = self.data.spotifyClient.GetPlaylist(spotifyId)
                        
                    except Exception as ex:
                        
                        _logsi.LogException("Unable to load spotify playlist '%s'. Continuing without playlist data" % context.Uri, ex)
                        self._playlist = None
                        
                else:
                    
                    self._playlist = None
                    
            # if no exception, then assume it was successful.
            self._attr_available = True

        except SpotifyWebApiError as ex:
            
            self._attr_available = False
            _logsi.LogException(None, ex)
            raise HomeAssistantError(ex.Message) from ex
        
        except Exception as ex:

            self._attr_available = False
            _logsi.LogException(None, ex)
            raise HomeAssistantError(str(ex)) from ex

        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug)


    @callback
    def _handle_devices_update(self) -> None:
        """
        Handle updated data from the coordinator.
        """
        if not self.enabled:
            return
        
        # inform HA of our current state.
        self.async_write_ha_state()


    def _UpdateNowPlayingData(self, config:PlayerPlayState) -> None:
        """
        Updates all media_player attributes that have to do with now playing information.
        """
        # update playing state.
        if config.IsPlaying == True:
            self._attr_state = MediaPlayerState.PLAYING
        elif config.IsPlaying == False:
            self._attr_state = MediaPlayerState.PAUSED
        else:
            self._attr_state = MediaPlayerState.IDLE
        
        # update volume level attribute.
        self._attr_volume_level = None
        if config.Device is not None:
            self._attr_volume_level = float(config.Device.VolumePercent / 100)

        # update seek-related attributes.
        if config.ProgressMS is not None:
            self._attr_media_position = config.ProgressMS / 1000
            self._attr_media_position_updated_at = utcnow()
        self._attr_media_duration = None
        if config.Item is not None:
            self._attr_media_duration = config.Item.DurationMS / 1000
        
        # update shuffle related attributes.
        self._attr_shuffle = config.IsShuffleEnabled
        
        # update repeat related attributes.
        self._attr_repeat = None
        if config.RepeatState is not None:
            if config.RepeatState == 'context':
                self._attr_repeat = RepeatMode.ALL.value
            elif config.RepeatState == 'track':
                self._attr_repeat = RepeatMode.ONE.value
            else:
                self._attr_repeat = RepeatMode.OFF.value

    # -----------------------------------------------------------------------------------
    # Custom Services
    # -----------------------------------------------------------------------------------

    def service_spotify_get_album(self, 
                                  albumId:str, 
                                  market:str=None,
                                  ) -> dict:
        """
        Get Spotify catalog information for a single album.
        
        Args:
            albumId (str):  
                The Spotify ID of the album.  
                Example: `6vc9OTcyd3hyzabCmsdnwE`
            market (str):
                An ISO 3166-1 alpha-2 country code. If a country code is specified, only content that 
                is available in that market will be returned.  If a valid user access token is specified 
                in the request header, the country associated with the user account will take priority over 
                this parameter.  
                Note: If neither market or user country are provided, the content is considered unavailable for the client.  
                Users can view the country that is associated with their account in the account settings.  
                Example: `ES`
                
        Returns:
            A dictionary that contains the following keys:
            - user_profile: A (partial) user profile that retrieved the result.
            - result: An `Album` object that contains the album details.
        """
        apiMethodName:str = 'service_spotify_get_album'
        apiMethodParms:SIMethodParmListContext = None
        result:Album = None

        try:

            # trace.
            apiMethodParms = _logsi.EnterMethodParmList(SILevel.Debug, apiMethodName)
            apiMethodParms.AppendKeyValue("albumId", albumId)
            apiMethodParms.AppendKeyValue("market", market)
            _logsi.LogMethodParmList(SILevel.Verbose, "Spotify Get Album Service", apiMethodParms)
                
            # request information from Spotify Web API.
            _logsi.LogVerbose(STAppMessages.MSG_SERVICE_QUERY_WEB_API)
            result = self.data.spotifyClient.GetAlbum(albumId, market)

            # return the (partial) user profile that retrieved the result, as well as the result itself.
            return {
                "user_profile": self._GetUserProfilePartialDictionary(self.data.spotifyClient.UserProfile),
                "result": result.ToDictionary()
            }

        # the following exceptions have already been logged, so we just need to
        # pass them back to HA for display in the log (or service UI).
        except SpotifyApiError as ex:
            raise HomeAssistantError(ex.Message)
        except SpotifyWebApiError as ex:
            raise HomeAssistantError(ex.Message)
        
        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug, apiMethodName)


    def service_spotify_get_album_favorites(self, 
                                            limit:int, 
                                            offset:int,
                                            market:str,
                                            limitTotal:int=None
                                            ) -> dict:
        """
        Get a list of the albums saved in the current Spotify user's 'Your Library'.
        
        Args:
            limit (int):
                The maximum number of items to return in a page of items.  
                Default: 20, Range: 1 to 50.  
            offset (int):
                The index of the first item to return.  
                Use with limit to get the next set of items.  
                Default: 0 (the first item).  
            market (str):
                An ISO 3166-1 alpha-2 country code. If a country code is specified, only content that 
                is available in that market will be returned.  If a valid user access token is specified 
                in the request header, the country associated with the user account will take priority over 
                this parameter.  
                Example: `ES`
            limitTotal (int):
                The maximum number of items to return for the request.
                If specified, this argument overrides the limit and offset argument values
                and paging is automatically used to retrieve all available items up to the
                maximum count specified.
                Default: None (disabled)
                
        Returns:
            A dictionary that contains the following keys:
            - user_profile: A (partial) user profile that retrieved the result.
            - result: A `AlbumPageSaved` object that contains playlist information.
        """
        apiMethodName:str = 'service_spotify_get_album_favorites'
        apiMethodParms:SIMethodParmListContext = None

        try:

            # trace.
            apiMethodParms = _logsi.EnterMethodParmList(SILevel.Debug, apiMethodName)
            apiMethodParms.AppendKeyValue("limit", limit)
            apiMethodParms.AppendKeyValue("offset", offset)
            apiMethodParms.AppendKeyValue("market", market)
            apiMethodParms.AppendKeyValue("limitTotal", limitTotal)
            _logsi.LogMethodParmList(SILevel.Verbose, "Spotify Get Album Favorites Service", apiMethodParms)
                
            # request information from Spotify Web API.
            _logsi.LogVerbose(STAppMessages.MSG_SERVICE_QUERY_WEB_API)
            result:AlbumPageSaved = self.data.spotifyClient.GetAlbumFavorites(limit, offset, market, limitTotal)

            # return the (partial) user profile that retrieved the result, as well as the result itself.
            return {
                "user_profile": self._GetUserProfilePartialDictionary(self.data.spotifyClient.UserProfile),
                "result": result.ToDictionary()
            }

        # the following exceptions have already been logged, so we just need to
        # pass them back to HA for display in the log (or service UI).
        except SpotifyApiError as ex:
            raise HomeAssistantError(ex.Message)
        except SpotifyWebApiError as ex:
            raise HomeAssistantError(ex.Message)
        
        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug, apiMethodName)


    def service_spotify_get_album_new_releases(self, 
                                               limit:int, 
                                               offset:int,
                                               country:str,
                                               limitTotal:int=None
                                               ) -> dict:
        """
        Get a list of new album releases featured in Spotify.
        
        Args:
            limit (int):
                The maximum number of items to return in a page of items.  
                Default: 20, Range: 1 to 50.  
            offset (int):
                The index of the first item to return.  
                Use with limit to get the next set of items.  
                Default: 0 (the first item).  
            country (str):
                An ISO 3166-1 alpha-2 country code. If a country code is specified, only content that 
                is available in that market will be returned.  If a valid user access token is specified 
                in the request header, the country associated with the user account will take priority over 
                this parameter.  
                Example: `ES`
            limitTotal (int):
                The maximum number of items to return for the request.
                If specified, this argument overrides the limit and offset argument values
                and paging is automatically used to retrieve all available items up to the
                maximum count specified.
                Default: None (disabled)
                
        Returns:
            A dictionary that contains the following keys:
            - user_profile: A (partial) user profile that retrieved the result.
            - result: A `AlbumPageSimplified` object that contains playlist information.
        """
        apiMethodName:str = 'service_spotify_get_album_new_releases'
        apiMethodParms:SIMethodParmListContext = None

        try:

            # trace.
            apiMethodParms = _logsi.EnterMethodParmList(SILevel.Debug, apiMethodName)
            apiMethodParms.AppendKeyValue("limit", limit)
            apiMethodParms.AppendKeyValue("offset", offset)
            apiMethodParms.AppendKeyValue("country", country)
            apiMethodParms.AppendKeyValue("limitTotal", limitTotal)
            _logsi.LogMethodParmList(SILevel.Verbose, "Spotify Get Album New Releases Service", apiMethodParms)
                
            # request information from Spotify Web API.
            _logsi.LogVerbose(STAppMessages.MSG_SERVICE_QUERY_WEB_API)
            result:AlbumPageSimplified = self.data.spotifyClient.GetAlbumNewReleases(limit, offset, country, limitTotal)

            # return the (partial) user profile that retrieved the result, as well as the result itself.
            return {
                "user_profile": self._GetUserProfilePartialDictionary(self.data.spotifyClient.UserProfile),
                "result": result.ToDictionary()
            }

        # the following exceptions have already been logged, so we just need to
        # pass them back to HA for display in the log (or service UI).
        except SpotifyApiError as ex:
            raise HomeAssistantError(ex.Message)
        except SpotifyWebApiError as ex:
            raise HomeAssistantError(ex.Message)
        
        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug, apiMethodName)


    def service_spotify_get_artist(self, 
                                  artistId:str, 
                                  ) -> dict:
        """
        Get Spotify catalog information for a single artist.
        
        Args:
            artistId (str):  
                The Spotify ID of the artist.  
                Example: `6APm8EjxOHSYM5B4i3vT3q`
                
        Returns:
            A dictionary that contains the following keys:
            - user_profile: A (partial) user profile that retrieved the result.
            - result: An `Album` object that contains the artist details.
        """
        apiMethodName:str = 'service_spotify_get_artist'
        apiMethodParms:SIMethodParmListContext = None
        result:Artist = None

        try:

            # trace.
            apiMethodParms = _logsi.EnterMethodParmList(SILevel.Debug, apiMethodName)
            apiMethodParms.AppendKeyValue("artistId", artistId)
            _logsi.LogMethodParmList(SILevel.Verbose, "Spotify Get Artist Service", apiMethodParms)
                
            # request information from Spotify Web API.
            _logsi.LogVerbose(STAppMessages.MSG_SERVICE_QUERY_WEB_API)
            result = self.data.spotifyClient.GetArtist(artistId)

            # return the (partial) user profile that retrieved the result, as well as the result itself.
            return {
                "user_profile": self._GetUserProfilePartialDictionary(self.data.spotifyClient.UserProfile),
                "result": result.ToDictionary()
            }

        # the following exceptions have already been logged, so we just need to
        # pass them back to HA for display in the log (or service UI).
        except SpotifyApiError as ex:
            raise HomeAssistantError(ex.Message)
        except SpotifyWebApiError as ex:
            raise HomeAssistantError(ex.Message)
        
        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug, apiMethodName)


    def service_spotify_get_artist_albums(self, 
                                          artistId:str, 
                                          include_groups:str='album', 
                                          limit:int=20, 
                                          offset:int=0,
                                          market:str=None,
                                          limitTotal:int=None
                                          ) -> dict:
        """
        Get Spotify catalog information about an artist's albums.
        
        Args:
            artistId (str):  
                The Spotify ID of the artist.  
                Example: `6APm8EjxOHSYM5B4i3vT3q`
            include_groups (str):  
                A comma-separated list of keywords that will be used to filter the response.  
                If not supplied, all album types will be returned.  
                Valid values are: `album`, `single`, `appears_on`, `compilation`  
                Example: `single,appears_on`
            limit (int):  
                The maximum number of items to return in a page of items.  
                Default: 20, Range: 1 to 50.  
            offset (int):  
                The index of the first item to return; use with limit to get the next set of items.  
                Default: 0 (the first item).  
            market (str):  
                An ISO 3166-1 alpha-2 country code. If a country code is specified, only content that is 
                available in that market will be returned.
                If a valid user access token is specified in the request header, the country associated with 
                the user account will take priority over this parameter.
                Example: `ES`
            limitTotal (int):
                The maximum number of items to return for the request.
                If specified, this argument overrides the limit and offset argument values
                and paging is automatically used to retrieve all available items up to the
                maximum count specified.
                Default: None (disabled)
                
        Returns:
            A dictionary that contains the following keys:
            - user_profile: A (partial) user profile that retrieved the result.
            - result: An `AlbumPageSimplified` object that contains artist album information.
        """
        apiMethodName:str = 'service_spotify_get_artist_albums'
        apiMethodParms:SIMethodParmListContext = None

        try:

            # trace.
            apiMethodParms = _logsi.EnterMethodParmList(SILevel.Debug, apiMethodName)
            apiMethodParms.AppendKeyValue("artistId", artistId)
            apiMethodParms.AppendKeyValue("include_groups", include_groups)
            apiMethodParms.AppendKeyValue("limit", limit)
            apiMethodParms.AppendKeyValue("offset", offset)
            apiMethodParms.AppendKeyValue("market", market)
            apiMethodParms.AppendKeyValue("limitTotal", limitTotal)
            _logsi.LogMethodParmList(SILevel.Verbose, "Spotify Get Artist Albums Service", apiMethodParms)
                
            # request information from Spotify Web API.
            _logsi.LogVerbose(STAppMessages.MSG_SERVICE_QUERY_WEB_API)
            result:AlbumPageSimplified = self.data.spotifyClient.GetArtistAlbums(artistId, include_groups, limit, offset, market, limitTotal)

            # return the (partial) user profile that retrieved the result, as well as the result itself.
            return {
                "user_profile": self._GetUserProfilePartialDictionary(self.data.spotifyClient.UserProfile),
                "result": result.ToDictionary()
            }

        # the following exceptions have already been logged, so we just need to
        # pass them back to HA for display in the log (or service UI).
        except SpotifyApiError as ex:
            raise HomeAssistantError(ex.Message)
        except SpotifyWebApiError as ex:
            raise HomeAssistantError(ex.Message)
        
        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug, apiMethodName)


    def service_spotify_get_artists_followed(self, 
                                             after:str, 
                                             limit:int,
                                             limitTotal:int=None
                                             ) -> dict:
        """
        Get the current user's followed artists.

        Args:
            after (str):
                The last artist ID retrieved from the previous request, or null for
                the first request.  
                Example: `0I2XqVXqHScXjHhk6AYYRe`  
            limit (int):
                The maximum number of items to return in a page of items.  
                Default: 20, Range: 1 to 50.  
            limitTotal (int):
                The maximum number of items to return for the request.
                If specified, this argument overrides the limit and offset argument values
                and paging is automatically used to retrieve all available items up to the
                maximum count specified.
                Default: None (disabled)
                
        Returns:
            A dictionary that contains the following keys:
            - user_profile: A (partial) user profile that retrieved the result.
            - result: An `ArtistPage` object that contains artist information.
        """
        apiMethodName:str = 'service_spotify_get_artists_followed'
        apiMethodParms:SIMethodParmListContext = None

        try:

            # trace.
            apiMethodParms = _logsi.EnterMethodParmList(SILevel.Debug, apiMethodName)
            apiMethodParms.AppendKeyValue("after", after)
            apiMethodParms.AppendKeyValue("limit", limit)
            apiMethodParms.AppendKeyValue("limitTotal", limitTotal)
            _logsi.LogMethodParmList(SILevel.Verbose, "Spotify Get Artists Followed Service", apiMethodParms)
                
            # request information from Spotify Web API.
            _logsi.LogVerbose(STAppMessages.MSG_SERVICE_QUERY_WEB_API)
            result:ArtistPage = self.data.spotifyClient.GetArtistsFollowed(after, limit, limitTotal)

            # return the (partial) user profile that retrieved the result, as well as the result itself.
            return {
                "user_profile": self._GetUserProfilePartialDictionary(self.data.spotifyClient.UserProfile),
                "result": result.ToDictionary()
            }

        # the following exceptions have already been logged, so we just need to
        # pass them back to HA for display in the log (or service UI).
        except SpotifyApiError as ex:
            raise HomeAssistantError(ex.Message)
        except SpotifyWebApiError as ex:
            raise HomeAssistantError(ex.Message)
        
        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug, apiMethodName)


    def service_spotify_get_browse_categorys_list(self, 
                                                  country:str=None, 
                                                  locale:str=None,
                                                  refresh:str=False,
                                                  ) -> dict:
        """
        Get a sorted list of ALL categories used to tag items in Spotify.
        
        Args:
            country (str):
                A country: an ISO 3166-1 alpha-2 country code.  
                Provide this parameter if you want the list of returned items to be relevant to a 
                particular country. If omitted, the returned items will be relevant to all countries.  
                Example: `SE`
            locale (str):
                The desired language, consisting of a lowercase ISO 639-1 language code and an uppercase 
                ISO 3166-1 alpha-2 country code, joined by an underscore.  
                For example: `es_MX`, meaning "Spanish (Mexico)".  
                Provide this parameter if you want the results returned in a particular language (where available).  
                Note: if locale is not supplied, or if the specified language is not available, all strings will 
                be returned in the Spotify default language (American English). The locale parameter, combined with 
                the country parameter, may give odd results if not carefully matched. For example country=`SE` and
                locale=`de_DE` will return a list of categories relevant to Sweden but as German language strings.  
                Example: `sv_SE`  
            refresh (bool):
                True to return real-time information from the spotify web api and
                update the cache; otherwise, False to just return the cached value.       
                
        Returns:
            A dictionary that contains the following keys:
            - user_profile: A (partial) user profile that retrieved the result.
            - result: A `CategoryPage` object that contains the list category details.
        """
        apiMethodName:str = 'service_spotify_get_browse_categorys_list'
        apiMethodParms:SIMethodParmListContext = None
        result:CategoryPage = CategoryPage()

        try:

            # trace.
            apiMethodParms = _logsi.EnterMethodParmList(SILevel.Debug, apiMethodName)
            apiMethodParms.AppendKeyValue("country", country)
            apiMethodParms.AppendKeyValue("locale", locale)
            apiMethodParms.AppendKeyValue("refresh", refresh)
            _logsi.LogMethodParmList(SILevel.Verbose, "Spotify Get Browse Categorys Service", apiMethodParms)
                
            # request information from Spotify Web API.
            _logsi.LogVerbose(STAppMessages.MSG_SERVICE_QUERY_WEB_API)
            resultArray:list[Category] = self.data.spotifyClient.GetBrowseCategorysList(country, locale, refresh)
            
            # build a CategoryPage object so we can convert to a dictionary.
            category:Category
            for category in resultArray:
                result.Items.append(category)

            # return the (partial) user profile that retrieved the result, as well as the result itself.
            return {
                "user_profile": self._GetUserProfilePartialDictionary(self.data.spotifyClient.UserProfile),
                "result": result.ToDictionary()
            }

        # the following exceptions have already been logged, so we just need to
        # pass them back to HA for display in the log (or service UI).
        except SpotifyApiError as ex:
            raise HomeAssistantError(ex.Message)
        except SpotifyWebApiError as ex:
            raise HomeAssistantError(ex.Message)
        
        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug, apiMethodName)


    def service_spotify_get_category_playlists(self, 
                                               categoryId:str=None,
                                               limit:int=20, 
                                               offset:int=0,
                                               country:str=None,
                                               limitTotal:int=None
                                               ) -> dict:
        """
        Get a list of Spotify playlists tagged with a particular category.
        
        Args:
            categoryId (str):
                The Spotify category ID for the category.  
                Example: `dinner`
            limit (int):
                The maximum number of items to return in a page of items.  
                Default: 20, Range: 1 to 50.  
            offset (int):
                The page index offset of the first item to return.  
                Use with limit to get the next set of items.  
                Default: 0 (the first item).  
            country (str):
                A country: an ISO 3166-1 alpha-2 country code.  
                Provide this parameter if you want the list of returned items to be relevant to a 
                particular country. If omitted, the returned items will be relevant to all countries.  
                Example: `SE`
            limitTotal (int):
                The maximum number of items to return for the request.  
                If specified, this argument overrides the limit and offset argument values
                and paging is automatically used to retrieve all available items up to the
                maximum number specified.  
                Default: None (disabled)
                
        Returns:
            A dictionary that contains the following keys:
            - user_profile: A (partial) user profile that retrieved the result.
            - result: An `PlaylistPageSimplified` object that contains playlist information.
            - message: string that describes what was returned (e.g. 'Popular Playlists').
        """
        apiMethodName:str = 'service_spotify_get_category_playlists'
        apiMethodParms:SIMethodParmListContext = None

        try:

            # trace.
            apiMethodParms = _logsi.EnterMethodParmList(SILevel.Debug, apiMethodName)
            apiMethodParms.AppendKeyValue("categoryId", categoryId)
            apiMethodParms.AppendKeyValue("limit", limit)
            apiMethodParms.AppendKeyValue("offset", offset)
            apiMethodParms.AppendKeyValue("country", country)
            apiMethodParms.AppendKeyValue("limitTotal", limitTotal)
            _logsi.LogMethodParmList(SILevel.Verbose, "Spotify Get Category Playlists Service", apiMethodParms)
                
            # request information from Spotify Web API.
            # have to treat this one a little bit differently due to return of a Tuple[] value.
            _logsi.LogVerbose(STAppMessages.MSG_SERVICE_QUERY_WEB_API)
            response:Tuple[PlaylistPageSimplified, str]
            response = self.data.spotifyClient.GetCategoryPlaylists(categoryId, limit, offset, country, limitTotal)
            _logsi.LogObject(SILevel.Verbose, "response Tuple object", response)

            result:PlaylistPageSimplified = response[0]
            message:str = response[1]

            # return the (partial) user profile that retrieved the result, as well as the result itself.
            return {
                "user_profile": self._GetUserProfilePartialDictionary(self.data.spotifyClient.UserProfile),
                "result": result.ToDictionary(),
                "message": message
            }

        # the following exceptions have already been logged, so we just need to
        # pass them back to HA for display in the log (or service UI).
        except SpotifyApiError as ex:
            raise HomeAssistantError(ex.Message)
        except SpotifyWebApiError as ex:
            raise HomeAssistantError(ex.Message)
        
        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug, apiMethodName)


    def service_spotify_get_featured_playlists(self, 
                                               limit:int=20, 
                                               offset:int=0,
                                               country:str=None,
                                               locale:str=None,
                                               timestamp:str=None,
                                               limitTotal:int=None
                                               ) -> dict:
        """
        Get a list of Spotify featured playlists (shown, for example, on a Spotify player's 'Browse' tab).
        
        Args:
            limit (int):
                The maximum number of items to return in a page of items.  
                Default: 20, Range: 1 to 50.  
            offset (int):
                The page index offset of the first item to return.  
                Use with limit to get the next set of items.  
                Default: 0 (the first item).  
            country (str):
                A country: an ISO 3166-1 alpha-2 country code.  
                Provide this parameter if you want the list of returned items to be relevant to a 
                particular country. If omitted, the returned items will be relevant to all countries.  
                Example: `SE`
            locale (str):
                The desired language, consisting of a lowercase ISO 639-1 language code and an uppercase 
                ISO 3166-1 alpha-2 country code, joined by an underscore.  
                For example: `es_MX`, meaning "Spanish (Mexico)".  
                Provide this parameter if you want the results returned in a particular language (where available).  
                Note: if locale is not supplied, or if the specified language is not available, all strings will 
                be returned in the Spotify default language (American English). The locale parameter, combined with 
                the country parameter, may give odd results if not carefully matched. For example country=`SE` and
                locale=`de_DE` will return a list of categories relevant to Sweden but as German language strings.  
                Example: `sv_SE`
            timestamp (str):
                A timestamp in ISO 8601 format: yyyy-MM-ddTHH:mm:ss.  
                Use this parameter to specify the user's local time to get results tailored for that specific date 
                and time in the day. If not provided, the response defaults to the current UTC time. 
                Example: `2023-10-23T09:00:00` for a user whose local time is 9AM. 
                If there were no featured playlists (or there is no data) at the specified time, the response will 
                revert to the current UTC time.
                Example: `2023-10-23T09:00:00`
            limitTotal (int):
                The maximum number of items to return for the request.  
                If specified, this argument overrides the limit and offset argument values
                and paging is automatically used to retrieve all available items up to the
                maximum number specified.  
                Default: None (disabled)
                
        Returns:
            A dictionary that contains the following keys:
            - user_profile: A (partial) user profile that retrieved the result.
            - result: An `PlaylistPageSimplified` object that contains playlist information.
            - message: string that describes what was returned (e.g. 'Popular Playlists').
        """
        apiMethodName:str = 'service_spotify_get_featured_playlists'
        apiMethodParms:SIMethodParmListContext = None

        try:

            # trace.
            apiMethodParms = _logsi.EnterMethodParmList(SILevel.Debug, apiMethodName)
            apiMethodParms.AppendKeyValue("limit", limit)
            apiMethodParms.AppendKeyValue("offset", offset)
            apiMethodParms.AppendKeyValue("country", country)
            apiMethodParms.AppendKeyValue("locale", locale)
            apiMethodParms.AppendKeyValue("timestamp", timestamp)
            apiMethodParms.AppendKeyValue("limitTotal", limitTotal)
            _logsi.LogMethodParmList(SILevel.Verbose, "Spotify Get Featured Playlists Service", apiMethodParms)
                
            # request information from Spotify Web API.
            # have to treat this one a little bit differently due to return of a Tuple[] value.
            _logsi.LogVerbose(STAppMessages.MSG_SERVICE_QUERY_WEB_API)
            response:Tuple[PlaylistPageSimplified, str]
            response = self.data.spotifyClient.GetFeaturedPlaylists(limit, offset, country, locale, timestamp, limitTotal)
            _logsi.LogObject(SILevel.Verbose, "response Tuple object", response)

            result:PlaylistPageSimplified = response[0]
            message:str = response[1]
                
            # return the (partial) user profile that retrieved the result, as well as the result itself.
            return {
                "user_profile": self._GetUserProfilePartialDictionary(self.data.spotifyClient.UserProfile),
                "result": result.ToDictionary(),
                "message": message
            }

        # the following exceptions have already been logged, so we just need to
        # pass them back to HA for display in the log (or service UI).
        except SpotifyApiError as ex:
            raise HomeAssistantError(ex.Message)
        except SpotifyWebApiError as ex:
            raise HomeAssistantError(ex.Message)
        
        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug, apiMethodName)


    def service_spotify_get_player_recent_tracks(self, 
                                                 limit:int, 
                                                 after:int, 
                                                 before:int,
                                                 limitTotal:int=None
                                                 ) -> dict:
        """
        Get tracks from the current user's recently played tracks.  
        Note: Currently doesn't support podcast episodes.
        
        This method requires the `user-read-recently-played` scope.
        
        Args:
            limit (int):
                The maximum number of items to return in a page of items.  
                Default: `20`, Range: `1` to `50`.  
            after (int):
                Returns all items after (but not including) this cursor position, which is 
                a Unix timestamp in milliseconds.  
                If `after` is specified, `before` must not be specified.  
                Use with limit to get the next set of items.  
                Default: `0` (the first item).  
            before (int):
                Returns all items before (but not including) this cursor position, which is 
                a Unix timestamp in milliseconds.  
                If `before` is specified, `after` must not be specified.  
                Use with limit to get the next set of items.  
                Default: `0` (the first item).  
            limitTotal (int):
                The maximum number of items to return for the request.  
                If specified, this argument overrides the limit and offset argument values
                and paging is automatically used to retrieve all available items up to the
                maximum number specified.  
                Default: None (disabled)
                
        The `after` and `before` arguments are based upon local time (not UTC time).  Recently
        played item history uses a local timestamp, and NOT a UTC timestamp.
                
        Returns:
            A dictionary that contains the following keys:
            - user_profile: A (partial) user profile that retrieved the result.
            - result: A `PlayHistoryPage` object that contains the playlist details.
        """
        apiMethodName:str = 'service_spotify_get_player_recent_tracks'
        apiMethodParms:SIMethodParmListContext = None
        result:PlayHistoryPage = None

        try:

            # trace.
            apiMethodParms = _logsi.EnterMethodParmList(SILevel.Debug, apiMethodName)
            apiMethodParms.AppendKeyValue("limit", limit)
            apiMethodParms.AppendKeyValue("after", after)
            apiMethodParms.AppendKeyValue("before", before)
            apiMethodParms.AppendKeyValue("limitTotal", limitTotal)
            _logsi.LogMethodParmList(SILevel.Verbose, "Spotify Get Player Recent Tracks", apiMethodParms)
                
            # request information from Spotify Web API.
            _logsi.LogVerbose(STAppMessages.MSG_SERVICE_QUERY_WEB_API)
            result = self.data.spotifyClient.GetPlayerRecentTracks(limit, after, before, limitTotal)

            # return the (partial) user profile that retrieved the result, as well as the result itself.
            return {
                "user_profile": self._GetUserProfilePartialDictionary(self.data.spotifyClient.UserProfile),
                "result": result.ToDictionary()
            }

        # the following exceptions have already been logged, so we just need to
        # pass them back to HA for display in the log (or service UI).
        except SpotifyApiError as ex:
            raise HomeAssistantError(ex.Message)
        except SpotifyWebApiError as ex:
            raise HomeAssistantError(ex.Message)
        
        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug, apiMethodName)


    def service_spotify_get_playlist(self, 
                                     playlistId:str, 
                                     market:str=None,
                                     fields:str=None,
                                     additionalTypes:str=None
                                     ) -> dict:
        """
        Get a playlist owned by a Spotify user.
        
        Args:
            playlistId (str):  
                The Spotify ID of the playlist.  
                Example: `5v5ETK9WFXAnGQ3MRubKuE`
            market (str):
                An ISO 3166-1 alpha-2 country code. If a country code is specified, only content that 
                is available in that market will be returned.  If a valid user access token is specified 
                in the request header, the country associated with the user account will take priority over 
                this parameter.  
                Example: `ES`
            fields (str):
                Filters for the query: a comma-separated list of the fields to return.  
                If omitted, all fields are returned. 
            additionalTypes (str):
                A comma-separated list of item types that your client supports besides the default track type.  
                Valid types are: track and episode.  
                
        Returns:
            A dictionary that contains the following keys:
            - user_profile: A (partial) user profile that retrieved the result.
            - result: A `Playlist` object that contains the playlist details.
        """
        apiMethodName:str = 'service_spotify_get_playlist'
        apiMethodParms:SIMethodParmListContext = None
        result:Playlist = None

        try:

            # trace.
            apiMethodParms = _logsi.EnterMethodParmList(SILevel.Debug, apiMethodName)
            apiMethodParms.AppendKeyValue("playlistId", playlistId)
            apiMethodParms.AppendKeyValue("market", market)
            apiMethodParms.AppendKeyValue("fields", fields)
            apiMethodParms.AppendKeyValue("additionalTypes", additionalTypes)
            _logsi.LogMethodParmList(SILevel.Verbose, "Spotify Get Playlist Service", apiMethodParms)
                
            # request information from Spotify Web API.
            _logsi.LogVerbose(STAppMessages.MSG_SERVICE_QUERY_WEB_API)
            result = self.data.spotifyClient.GetPlaylist(playlistId, market, fields, additionalTypes)

            # return the (partial) user profile that retrieved the result, as well as the result itself.
            return {
                "user_profile": self._GetUserProfilePartialDictionary(self.data.spotifyClient.UserProfile),
                "result": result.ToDictionary()
            }

        # the following exceptions have already been logged, so we just need to
        # pass them back to HA for display in the log (or service UI).
        except SpotifyApiError as ex:
            raise HomeAssistantError(ex.Message)
        except SpotifyWebApiError as ex:
            raise HomeAssistantError(ex.Message)
        
        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug, apiMethodName)


    def service_spotify_get_playlist_favorites(self, 
                                               limit:int, 
                                               offset:int,
                                               limitTotal:int=None
                                               ) -> dict:
        """
        Get a list of the playlists owned or followed by the current Spotify user.

        Args:
            limit (int):
                The maximum number of items to return in a page of items.  
                Default: 20, Range: 1 to 50.  
            offset (int):
                The index of the first item to return.  
                Use with limit to get the next set of items.  
                Default: 0 (the first item).  
            limitTotal (int):
                The maximum number of items to return for the request.
                If specified, this argument overrides the limit and offset argument values
                and paging is automatically used to retrieve all available items up to the
                maximum count specified.
                Default: None (disabled)

        Returns:
            A dictionary that contains the following keys:
            - user_profile: A (partial) user profile that retrieved the result.
            - result: A `PlaylistPageSimplified` object that contains playlist information.
        """
        apiMethodName:str = 'service_spotify_get_playlist_favorites'
        apiMethodParms:SIMethodParmListContext = None

        try:

            # trace.
            apiMethodParms = _logsi.EnterMethodParmList(SILevel.Debug, apiMethodName)
            apiMethodParms.AppendKeyValue("limit", limit)
            apiMethodParms.AppendKeyValue("offset", offset)
            apiMethodParms.AppendKeyValue("limitTotal", limitTotal)
            _logsi.LogMethodParmList(SILevel.Verbose, "Spotify Get Playlist Favorites Service", apiMethodParms)
                
            # request information from Spotify Web API.
            _logsi.LogVerbose(STAppMessages.MSG_SERVICE_QUERY_WEB_API)
            result:PlaylistPageSimplified = self.data.spotifyClient.GetPlaylistFavorites(limit, offset, limitTotal)

            # return the (partial) user profile that retrieved the result, as well as the result itself.
            return {
                "user_profile": self._GetUserProfilePartialDictionary(self.data.spotifyClient.UserProfile),
                "result": result.ToDictionary()
            }

        # the following exceptions have already been logged, so we just need to
        # pass them back to HA for display in the log (or service UI).
        except SpotifyApiError as ex:
            raise HomeAssistantError(ex.Message)
        except SpotifyWebApiError as ex:
            raise HomeAssistantError(ex.Message)
        
        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug, apiMethodName)


    def service_spotify_get_show(self, 
                                 showId:str, 
                                 market:str=None,
                                 ) -> dict:
        """
        Get Spotify catalog information for a single show identified by its unique Spotify ID.
        
        Args:
            showId (str):  
                The Spotify ID for the show.
                Example: `5CfCWKI5pZ28U0uOzXkDHe`
            market (str):
                An ISO 3166-1 alpha-2 country code. If a country code is specified, only content that 
                is available in that market will be returned.  If a valid user access token is specified 
                in the request header, the country associated with the user account will take priority over 
                this parameter.  
                Example: `ES`
                
        Returns:
            A dictionary that contains the following keys:
            - user_profile: A (partial) user profile that retrieved the result.
            - result: A `Show` object that contain the show details.
        """
        apiMethodName:str = 'service_spotify_get_show'
        apiMethodParms:SIMethodParmListContext = None
        result:Show = None

        try:

            # trace.
            apiMethodParms = _logsi.EnterMethodParmList(SILevel.Debug, apiMethodName)
            apiMethodParms.AppendKeyValue("showId", showId)
            apiMethodParms.AppendKeyValue("market", market)
            _logsi.LogMethodParmList(SILevel.Verbose, "Spotify Get Show Service", apiMethodParms)
                
            # request information from Spotify Web API.
            _logsi.LogVerbose(STAppMessages.MSG_SERVICE_QUERY_WEB_API)
            result = self.data.spotifyClient.GetShow(showId, market)

            # return the (partial) user profile that retrieved the result, as well as the result itself.
            return {
                "user_profile": self._GetUserProfilePartialDictionary(self.data.spotifyClient.UserProfile),
                "result": result.ToDictionary()
            }

        # the following exceptions have already been logged, so we just need to
        # pass them back to HA for display in the log (or service UI).
        except SpotifyApiError as ex:
            raise HomeAssistantError(ex.Message)
        except SpotifyWebApiError as ex:
            raise HomeAssistantError(ex.Message)
        
        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug, apiMethodName)


    def service_spotify_get_show_episodes(self, 
                                          showId:str, 
                                          limit:int, 
                                          offset:int,
                                          market:str=None,
                                          ) -> dict:
        """
        Get Spotify catalog information about a show's episodes.
        
        Args:
            showId (str):  
                The Spotify ID for the show.
                Example: `38bS44xjbVVZ3No3ByF1dJ`
            limit (int):  
                The maximum number of items to return in a page of items.  
                Default: 20, Range: 1 to 50.  
            offset (int):  
                The index of the first item to return; use with limit to get the next set of items.  
                Default: 0 (the first item).  
            market (str):
                An ISO 3166-1 alpha-2 country code. If a country code is specified, only content that 
                is available in that market will be returned.  If a valid user access token is specified 
                in the request header, the country associated with the user account will take priority over 
                this parameter.  
                Note: If neither market or user country are provided, the content is considered unavailable for the client.  
                Users can view the country that is associated with their account in the account settings.  
                Example: `ES`
                
        Returns:
            A dictionary that contains the following keys:
            - user_profile: A (partial) user profile that retrieved the result.
            - result: A `EpisodePageSimplified` object that contains the show episodes.
        """
        apiMethodName:str = 'service_spotify_get_show_episodes'
        apiMethodParms:SIMethodParmListContext = None
        result:EpisodePageSimplified = None

        try:

            # trace.
            apiMethodParms = _logsi.EnterMethodParmList(SILevel.Debug, apiMethodName)
            apiMethodParms.AppendKeyValue("showId", showId)
            apiMethodParms.AppendKeyValue("limit", limit)
            apiMethodParms.AppendKeyValue("additionaoffsetlTypes", offset)
            apiMethodParms.AppendKeyValue("market", market)
            _logsi.LogMethodParmList(SILevel.Verbose, "Spotify Get Show Episodes Service", apiMethodParms)
                
            # request information from Spotify Web API.
            _logsi.LogVerbose(STAppMessages.MSG_SERVICE_QUERY_WEB_API)
            result = self.data.spotifyClient.GetShowEpisodes(showId, limit, offset, market)

            # return the (partial) user profile that retrieved the result, as well as the result itself.
            return {
                "user_profile": self._GetUserProfilePartialDictionary(self.data.spotifyClient.UserProfile),
                "result": result.ToDictionary()
            }

        # the following exceptions have already been logged, so we just need to
        # pass them back to HA for display in the log (or service UI).
        except SpotifyApiError as ex:
            raise HomeAssistantError(ex.Message)
        except SpotifyWebApiError as ex:
            raise HomeAssistantError(ex.Message)
        
        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug, apiMethodName)


    def service_spotify_get_show_favorites(self, 
                                            limit:int, 
                                            offset:int,
                                            limitTotal:int=None
                                            ) -> dict:
        """
        Get a list of the shows saved in the current Spotify user's 'Your Library'.
        
        Args:
            limit (int):
                The maximum number of items to return in a page of items.  
                Default: 20, Range: 1 to 50.  
            offset (int):
                The index of the first item to return.  
                Use with limit to get the next set of items.  
                Default: 0 (the first item).  
            limitTotal (int):
                The maximum number of items to return for the request.
                If specified, this argument overrides the limit and offset argument values
                and paging is automatically used to retrieve all available items up to the
                maximum count specified.
                Default: None (disabled)
                
        Returns:
            A dictionary that contains the following keys:
            - user_profile: A (partial) user profile that retrieved the result.
            - result: A `ShowPageSaved` object that contains playlist information.
        """
        apiMethodName:str = 'service_spotify_get_show_favorites'
        apiMethodParms:SIMethodParmListContext = None

        try:

            # trace.
            apiMethodParms = _logsi.EnterMethodParmList(SILevel.Debug, apiMethodName)
            apiMethodParms.AppendKeyValue("limit", limit)
            apiMethodParms.AppendKeyValue("offset", offset)
            apiMethodParms.AppendKeyValue("limitTotal", limitTotal)
            _logsi.LogMethodParmList(SILevel.Verbose, "Spotify Get Show Favorites Service", apiMethodParms)
                
            # request information from Spotify Web API.
            _logsi.LogVerbose(STAppMessages.MSG_SERVICE_QUERY_WEB_API)
            result:ShowPageSaved = self.data.spotifyClient.GetShowFavorites(limit, offset, limitTotal)

            # return the (partial) user profile that retrieved the result, as well as the result itself.
            return {
                "user_profile": self._GetUserProfilePartialDictionary(self.data.spotifyClient.UserProfile),
                "result": result.ToDictionary()
            }

        # the following exceptions have already been logged, so we just need to
        # pass them back to HA for display in the log (or service UI).
        except SpotifyApiError as ex:
            raise HomeAssistantError(ex.Message)
        except SpotifyWebApiError as ex:
            raise HomeAssistantError(ex.Message)
        
        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug, apiMethodName)


    def service_spotify_get_track_favorites(self, 
                                            limit:int, 
                                            offset:int,
                                            market:str,
                                            limitTotal:int=None
                                            ) -> dict:
        """
        Get a list of the tracks saved in the current Spotify user's 'Your Library'.
        
        Args:
            limit (int):
                The maximum number of items to return in a page of items.  
                Default: 20, Range: 1 to 50.  
            offset (int):
                The index of the first item to return.  
                Use with limit to get the next set of items.  
                Default: 0 (the first item).  
            market (str):
                An ISO 3166-1 alpha-2 country code. If a country code is specified, only content that 
                is available in that market will be returned.  If a valid user access token is specified 
                in the request header, the country associated with the user account will take priority over 
                this parameter.  
                Example: `ES`
            limitTotal (int):
                The maximum number of items to return for the request.
                If specified, this argument overrides the limit and offset argument values
                and paging is automatically used to retrieve all available items up to the
                maximum count specified.
                Default: None (disabled)
                
        Returns:
            A dictionary that contains the following keys:
            - user_profile: A (partial) user profile that retrieved the result.
            - result: A `TrackPageSaved` object that contains playlist information.
        """
        apiMethodName:str = 'service_spotify_get_track_favorites'
        apiMethodParms:SIMethodParmListContext = None
        result:TrackPageSaved = TrackPageSaved()

        try:

            # trace.
            apiMethodParms = _logsi.EnterMethodParmList(SILevel.Debug, apiMethodName)
            apiMethodParms.AppendKeyValue("limit", limit)
            apiMethodParms.AppendKeyValue("offset", offset)
            apiMethodParms.AppendKeyValue("market", market)
            apiMethodParms.AppendKeyValue("limitTotal", limitTotal)
            _logsi.LogMethodParmList(SILevel.Verbose, "Spotify Get Track Favorites Service", apiMethodParms)
                
            # request information from Spotify Web API.
            _logsi.LogVerbose(STAppMessages.MSG_SERVICE_QUERY_WEB_API)
            result:TrackPageSaved = self.data.spotifyClient.GetTrackFavorites(limit, offset, market, limitTotal)

            # return the (partial) user profile that retrieved the result, as well as the result itself.
            return {
                "user_profile": self._GetUserProfilePartialDictionary(self.data.spotifyClient.UserProfile),
                "result": result.ToDictionary()
            }

        # the following exceptions have already been logged, so we just need to
        # pass them back to HA for display in the log (or service UI).
        except SpotifyApiError as ex:
            raise HomeAssistantError(ex.Message)
        except SpotifyWebApiError as ex:
            raise HomeAssistantError(ex.Message)
        
        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug, apiMethodName)


    def service_spotify_get_users_top_artists(self, 
                                              timeRange:str,
                                              limit:int, 
                                              offset:int,
                                              limitTotal:int=None
                                              ) -> dict:
        """
        Get the current user's top artists based on calculated affinity.
        
        Args:
            timeRange (str):
                Over what time frame the affinities are computed.  
                Valid values:  
                - long_term (calculated from several years of data and including all new data as it becomes available).  
                - medium_term (approximately last 6 months).  
                - short_term (approximately last 4 weeks).  
                Default: `medium_term`  
                Example: `long_term`
            limit (int):
                The maximum number of items to return in a page of items.  
                Default: 20, Range: 1 to 50.  
            offset (int):
                The index of the first item to return.  
                Use with limit to get the next set of items.  
                Default: 0 (the first item), Range: 0 to 1000
            limitTotal (int):
                The maximum number of items to return for the request.
                If specified, this argument overrides the limit and offset argument values
                and paging is automatically used to retrieve all available items up to the
                maximum count specified.
                Default: None (disabled)
                
        Returns:
            A dictionary that contains the following keys:
            - user_profile: A (partial) user profile that retrieved the result.
            - result: An `ArtistPage` object of matching results.
        """
        apiMethodName:str = 'service_spotify_get_users_top_artists'
        apiMethodParms:SIMethodParmListContext = None

        try:

            # trace.
            apiMethodParms = _logsi.EnterMethodParmList(SILevel.Debug, apiMethodName)
            apiMethodParms.AppendKeyValue("timeRange", timeRange)
            apiMethodParms.AppendKeyValue("limit", limit)
            apiMethodParms.AppendKeyValue("offset", offset)
            apiMethodParms.AppendKeyValue("limitTotal", limitTotal)
            _logsi.LogMethodParmList(SILevel.Verbose, "Spotify Get Users Top Artists Service", apiMethodParms)
                
            # request information from Spotify Web API.
            _logsi.LogVerbose(STAppMessages.MSG_SERVICE_QUERY_WEB_API)
            result:ArtistPage = self.data.spotifyClient.GetUsersTopArtists(timeRange, limit, offset, limitTotal)

            # return the (partial) user profile that retrieved the result, as well as the result itself.
            return {
                "user_profile": self._GetUserProfilePartialDictionary(self.data.spotifyClient.UserProfile),
                "result": result.ToDictionary()
            }

        # the following exceptions have already been logged, so we just need to
        # pass them back to HA for display in the log (or service UI).
        except SpotifyApiError as ex:
            raise HomeAssistantError(ex.Message)
        except SpotifyWebApiError as ex:
            raise HomeAssistantError(ex.Message)
        
        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug, apiMethodName)


    def service_spotify_get_users_top_tracks(self, 
                                             timeRange:str,
                                             limit:int, 
                                             offset:int,
                                             limitTotal:int=None
                                             ) -> dict:
        """
        Get the current user's top tracks based on calculated affinity.
        
        Args:
            timeRange (str):
                Over what time frame the affinities are computed.  
                Valid values:  
                - long_term (calculated from several years of data and including all new data as it becomes available).  
                - medium_term (approximately last 6 months).  
                - short_term (approximately last 4 weeks).  
                Default: `medium_term`  
                Example: `long_term`
            limit (int):
                The maximum number of items to return in a page of items.  
                Default: 20, Range: 1 to 50.  
            offset (int):
                The index of the first item to return.  
                Use with limit to get the next set of items.  
                Default: 0 (the first item), Range: 0 to 1000
            limitTotal (int):
                The maximum number of items to return for the request.
                If specified, this argument overrides the limit and offset argument values
                and paging is automatically used to retrieve all available items up to the
                maximum count specified.
                Default: None (disabled)
                
        Returns:
            A dictionary that contains the following keys:
            - user_profile: A (partial) user profile that retrieved the result.
            - result: An `TrackPage` object of matching results.
        """
        apiMethodName:str = 'service_spotify_get_users_top_tracks'
        apiMethodParms:SIMethodParmListContext = None

        try:

            # trace.
            apiMethodParms = _logsi.EnterMethodParmList(SILevel.Debug, apiMethodName)
            apiMethodParms.AppendKeyValue("timeRange", timeRange)
            apiMethodParms.AppendKeyValue("limit", limit)
            apiMethodParms.AppendKeyValue("offset", offset)
            apiMethodParms.AppendKeyValue("limitTotal", limitTotal)
            _logsi.LogMethodParmList(SILevel.Verbose, "Spotify Get Users Top Tracks Service", apiMethodParms)
            
            # request information from Spotify Web API.
            _logsi.LogVerbose(STAppMessages.MSG_SERVICE_QUERY_WEB_API)
            result:TrackPage = self.data.spotifyClient.GetUsersTopTracks(timeRange, limit, offset, limitTotal)

            # return the (partial) user profile that retrieved the result, as well as the result itself.
            return {
                "user_profile": self._GetUserProfilePartialDictionary(self.data.spotifyClient.UserProfile),
                "result": result.ToDictionary()
            }

        # the following exceptions have already been logged, so we just need to
        # pass them back to HA for display in the log (or service UI).
        except SpotifyApiError as ex:
            raise HomeAssistantError(ex.Message)
        except SpotifyWebApiError as ex:
            raise HomeAssistantError(ex.Message)
        
        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug, apiMethodName)


    def service_spotify_search_playlists(self, 
                                         criteria:str, 
                                         limit:int, 
                                         offset:int, 
                                         market:str,
                                         includeExternal:str,
                                         limitTotal:int,
                                         spotifyOwnedOnly:bool
                                         ) -> dict:
        """
        Get Spotify catalog information about albums, artists, playlists, tracks, shows, episodes 
        or audiobooks that match a keyword string. Audiobooks are only available within the US, UK, 
        Canada, Ireland, New Zealand and Australia markets.

        Args:
            criteria (str):
                Your search query.  
                You can narrow down your search using field filters.  
                The available filters are album, artist, track, year, upc, tag:hipster, tag:new, 
                isrc, and genre. Each field filter only applies to certain result types.  
                The artist and year filters can be used while searching albums, artists and tracks.
                You can filter on a single year or a range (e.g. 1955-1960).  
                The album filter can be used while searching albums and tracks.  
                The genre filter can be used while searching artists and tracks.  
                The isrc and track filters can be used while searching tracks.  
                The upc, tag:new and tag:hipster filters can only be used while searching albums. 
                The tag:new filter will return albums released in the past two weeks and tag:hipster 
                can be used to return only albums with the lowest 10% popularity.
            limit (int):
                The maximum number of items to return in a page of items.  
                Default: 20, Range: 1 to 50.  
            offset (int):
                The page index offset of the first item to return.  
                Use with limit to get the next set of items.  
                Default: 0 (the first item).  Range: 0 to 1000.  
            market (str):
                An ISO 3166-1 alpha-2 country code. If a country code is specified, only content that 
                is available in that market will be returned.  If a valid user access token is specified 
                in the request header, the country associated with the user account will take priority over 
                this parameter.  
                Note: If neither market or user country are provided, the content is considered unavailable for the client.  
                Users can view the country that is associated with their account in the account settings.  
                Example: `ES`
            includeExternal (str):
                If "audio" is specified it signals that the client can play externally hosted audio content, and 
                marks the content as playable in the response. By default externally hosted audio content is marked 
                as unplayable in the response.  
                Allowed values: "audio"
            limitTotal (int):
                The maximum number of items to return for the request.  
                If specified, this argument overrides the limit and offset argument values
                and paging is automatically used to retrieve all available items up to the
                maximum number specified.  
                Default: None (disabled)
            spotifyOwnedOnly (bool):
                True to return only found items that are owned by spotify (e.g. content generated for you by the spotify AI engine); 
                otherwise, False to return all found items.   
                Default is False.

        Returns:
            A dictionary representation of a `SearchResponse` object that contain the results.
        """
        apiMethodName:str = 'service_spotify_search_playlists'
        apiMethodParms:SIMethodParmListContext = None
        result:SearchResponse = None

        try:

            # trace.
            apiMethodParms = _logsi.EnterMethodParmList(SILevel.Debug, apiMethodName)
            apiMethodParms.AppendKeyValue("criteria", criteria)
            apiMethodParms.AppendKeyValue("limit", limit)
            apiMethodParms.AppendKeyValue("offset", offset)
            apiMethodParms.AppendKeyValue("market", market)
            apiMethodParms.AppendKeyValue("includeExternal", includeExternal)
            apiMethodParms.AppendKeyValue("limitTotal", limitTotal)
            apiMethodParms.AppendKeyValue("spotifyOwnedOnly", spotifyOwnedOnly)
            _logsi.LogMethodParmList(SILevel.Verbose, "Spotify Search Service", apiMethodParms)
                
            # get Spotify catalog information about Playlists that match a keyword string.
            _logsi.LogVerbose("Searching Spotify Playlists for criteria")
            searchResponse:SearchResponse = self.data.spotifyClient.SearchPlaylists(criteria, limit, offset, market, includeExternal, limitTotal, spotifyOwnedOnly)

            # # sort items on Name property, ascending order.
            # if len(searchResponse.Playlists.Items) > 0:
            #     searchResponse.Playlists.Items.sort(key=lambda x: (x.Name or "").lower(), reverse=False)

            # return the (partial) user profile that retrieved the result, as well as the result itself.
            return {
                "user_profile": self._GetUserProfilePartialDictionary(self.data.spotifyClient.UserProfile),
                "result": searchResponse.Playlists.ToDictionary()
            }

        # the following exceptions have already been logged, so we just need to
        # pass them back to HA for display in the log (or service UI).
        except SpotifyApiError as ex:
            raise HomeAssistantError(ex.Message)
        except SpotifyWebApiError as ex:
            raise HomeAssistantError(ex.Message)
        
        finally:
        
            # trace.
            _logsi.LeaveMethod(SILevel.Debug, apiMethodName)


    def _GetUserProfilePartialDictionary(self, userProfile:UserProfile) -> dict:
        """
        Returns a dictionary of a partial UserProfile object that can be returned with 
        selected service results.  This allows the caller to know exactly what Spotify
        user made the given request.
        """
        # return the user profile that retrieved the result, as well as the result.
        return {
            "country": userProfile.Country,
            "display_name": userProfile.DisplayName,
            "email": userProfile.EMail,
            "id": userProfile.Id,
            "product": userProfile.Product,
            "type": userProfile.Type,
            "uri": userProfile.Uri,
        }


    async def async_added_to_hass(self) -> None:
        """
        Run when this Entity has been added to HA.

        Importantly for a push integration, the module that will be getting updates
        needs to notify HA of changes.  In our case, we created a DataUpdateCoordinator
        instance that will inform us when the Spotify Connect device list has changed.  We
        will register some callback methods here so that we can forward the change
        notifications on to Home Assistant (e.g. a call to `self.async_write_ha_state`).

        The call back registration is done once this entity is registered with Home
        Assistant (rather than in the `__init__` method).
        """
        try:

            # trace.
            _logsi.EnterMethod(SILevel.Debug)

            # call base class method.
            await super().async_added_to_hass()

            # add listener that will inform HA of our state if a user removes the device instance.
            _logsi.LogVerbose("'%s': adding '_handle_devices_update' listener" % self.name)
            self.async_on_remove(
                self.data.devices.async_add_listener(self._handle_devices_update)
            )

        finally:
                
            # trace.
            _logsi.LeaveMethod(SILevel.Debug)


    async def async_will_remove_from_hass(self) -> None:
        """
        Entity being removed from hass (the opposite of async_added_to_hass).

        Remove any registered call backs here.
        """
        try:

            # trace.
            _logsi.EnterMethod(SILevel.Debug)
            _logsi.LogVerbose("'%s': removing instance from hass" % self.name)
       
            # nothing to do here.

        finally:

            # trace.
            _logsi.LeaveMethod(SILevel.Debug)


    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """
        Implement the websocket media browsing helper.
        """
        methodParms:SIMethodParmListContext = None
        
        try:

            # trace.
            methodParms = _logsi.EnterMethodParmList(SILevel.Debug)
            methodParms.AppendKeyValue("media_content_type", media_content_type)
            methodParms.AppendKeyValue("media_content_id", media_content_id)
            _logsi.LogMethodParmList(SILevel.Verbose, "'%s': MediaPlayer is browsing for media" % self.name, methodParms)
            
            # browse spotifysplus device media.
            if media_content_type is None and media_content_id is None:

                # handle initial media browser selection (e.g. show the starting index).
                _logsi.LogVerbose("'%s': MediaPlayer is browsing main media library index content id '%s'" % (self.name, media_content_id))
                return await async_browse_media_library_index(
                    self.hass,
                    self.data.spotifyClient,
                    self.name,
                    self.source,
                    SPOTIFY_LIBRARY_MAP,
                    BrowsableMedia.SPOTIFY_LIBRARY_INDEX,
                    media_content_type,
                    media_content_id,
                )

            else:
                
                # handle spotifysplus media library selection.
                # note that this is NOT async, as SpotifyClient is not async!
                _logsi.LogVerbose("'%s': MediaPlayer is browsing media node content id '%s'" % (self.name, media_content_id))
                return await self.hass.async_add_executor_job(
                    browse_media_node,
                    self.hass,
                    self.data.spotifyClient,
                    self.name,
                    self.source,
                    SPOTIFY_LIBRARY_MAP,
                    media_content_type,
                    media_content_id,
                )

        except Exception as ex:
            
            # trace.
            _logsi.LogException("'%s': MediaPlayer async_browse_media exception: %s" % (self.name, str(ex)), ex, logToSystemLogger=False)
            raise HomeAssistantError(str(ex)) from ex
        
        finally:

            # trace.
            _logsi.LeaveMethod(SILevel.Debug)
