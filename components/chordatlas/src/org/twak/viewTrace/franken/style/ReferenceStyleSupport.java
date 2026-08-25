package org.twak.viewTrace.franken.style;

import java.awt.Graphics2D;
import java.awt.image.BufferedImage;
import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.Iterator;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import javax.imageio.ImageIO;
import javax.imageio.ImageReader;
import javax.imageio.stream.ImageInputStream;

import org.twak.tweed.TweedSettings;
import org.twak.viewTrace.franken.FacadeTexApp;
import org.twak.viewTrace.franken.NetInfo;
import org.twak.viewTrace.franken.RoofTexApp;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

/** Shared validation and file contracts for image-conditioned style vectors. */
public final class ReferenceStyleSupport {

	public static final int STYLE_DIMENSIONS = 8;
	private static final long MAX_REFERENCE_BYTES = 32L * 1024L * 1024L;
	private static final long MAX_REFERENCE_PIXELS = 4096L * 4096L;
	private static final int MAX_REFERENCE_DIMENSION = 8192;
	private static final long MAX_READY_MARKER_AGE_MILLIS = 20_000L;
	private static final long MAX_READY_MARKER_FUTURE_MILLIS = 5_000L;
	private static final long MAX_READY_MARKER_BYTES = 64L * 1024L;
	private static final long MAX_ROOF_MANIFEST_BYTES = 1024L * 1024L;
	private static final long MAX_ROOF_OUTPUT_BYTES = 64L * 1024L * 1024L;
	private static final ObjectMapper JSON = new ObjectMapper();
	private static final Map<String, Object> ENCODER_LOCKS = new ConcurrentHashMap<>();
	private static final String[][] ROOF_OUTPUTS = new String[][] {
			{ "satellite_north_up", "satellite_north_up.png" },
			{ "source_valid_mask", "source_valid_mask.png" },
			{ "footprint_mask", "footprint_mask.png" },
			{ "roof_style_mask", "roof_style_mask.png" },
			{ "roof_reference", "roof_reference.png" },
			{ "roof_reference_rgba", "roof_reference_rgba.png" }
	};

	private ReferenceStyleSupport() {}

	public static boolean supportsReferenceImage( Class<?> target ) {
		return target == FacadeTexApp.class || target == RoofTexApp.class;
	}

	public static String loadPrompt( Class<?> target ) {
		if ( target == RoofTexApp.class )
			return "Load/drop satellite roof reference";
		if ( target == FacadeTexApp.class )
			return "Load/drop facade reference";
		return "Load/drop style reference";
	}

	/** Shared per-encoder mutex because each FrankenGAN encoder has one fixed go file. */
	public static Object encoderLock( String networkName ) {
		if ( networkName == null )
			throw new IllegalArgumentException( "Missing encoder network name." );
		Object created = new Object();
		Object existing = ENCODER_LOCKS.putIfAbsent( networkName, created );
		return existing == null ? created : existing;
	}

	/**
	 * Returns null when the compatibility watcher exposes this encoder, otherwise
	 * a user-facing reason. The ready marker avoids waiting for a dead/stale input
	 * directory left by an earlier watcher process.
	 */
	public static String encoderUnavailableReason( NetInfo network ) {
		File root = configuredFrankenGanRoot();
		return network == null
				? "No FrankenGAN network is configured for this material."
				: encoderUnavailableReason( root, network.name, network.sizeZ, STYLE_DIMENSIONS );
	}

	public static String encoderUnavailableReason( String networkName, int networkDimensions,
			int expectedDimensions ) {
		File root = configuredFrankenGanRoot();
		return encoderUnavailableReason( root, networkName, networkDimensions, expectedDimensions );
	}

	private static File configuredFrankenGanRoot() {
		if ( TweedSettings.settings == null || TweedSettings.settings.bikeGanRoot == null
				|| TweedSettings.settings.bikeGanRoot.trim().isEmpty() )
			return null;
		return new File( TweedSettings.settings.bikeGanRoot );
	}

	static String encoderUnavailableReason( File root, String networkName, int networkDimensions,
			int expectedDimensions ) {
		return encoderUnavailableReason( root, networkName, networkDimensions, expectedDimensions,
				System.currentTimeMillis() );
	}

	static String encoderUnavailableReason( File root, String networkName, int networkDimensions,
			int expectedDimensions, long nowMillis ) {
		if ( networkName == null || networkName.trim().isEmpty() )
			return "No FrankenGAN network is configured for this material.";
		if ( networkDimensions != expectedDimensions || expectedDimensions != STYLE_DIMENSIONS )
			return "The selected network does not expose an 8-dimensional style encoder.";
		if ( root == null || !root.isDirectory() )
			return "FrankenGAN root is missing. Check bikeGanRoot in tweed.xml.";
		File ready = new File( root, ".myproject_frankengan_ready.json" );
		if ( !ready.isFile() || ready.length() == 0 )
			return "FrankenGAN encoder is not ready. Start the myProject compatibility watcher.";
		String invalidMarker = invalidReadyMarkerReason( ready, nowMillis );
		if ( invalidMarker != null )
			return invalidMarker;
		File input = new File( root, "input" + File.separator + networkName + "_e"
				+ File.separator + "val" );
		if ( !input.isDirectory() )
			return "FrankenGAN encoder input is unavailable for " + networkName + ".";
		return null;
	}

	private static String invalidReadyMarkerReason( File ready, long nowMillis ) {
		if ( ready.length() > MAX_READY_MARKER_BYTES )
			return "FrankenGAN ready marker is invalid; restart the compatibility watcher.";
		try {
			JsonNode payload = readBoundedJson( ready, MAX_READY_MARKER_BYTES );
			JsonNode pid = payload == null ? null : payload.get( "pid" );
			JsonNode token = payload == null ? null : payload.get( "token" );
			JsonNode heartbeat = payload == null ? null : payload.get( "heartbeat_epoch" );
			if ( payload == null || !payload.isObject()
					|| pid == null || !pid.isIntegralNumber() || pid.asLong() <= 0
					|| token == null || !token.isTextual() || token.asText().trim().isEmpty()
					|| heartbeat == null || !heartbeat.isNumber() )
				return "FrankenGAN ready marker is invalid; restart the compatibility watcher.";
			double heartbeatSeconds = heartbeat.asDouble();
			if ( Double.isNaN( heartbeatSeconds ) || Double.isInfinite( heartbeatSeconds )
					|| heartbeatSeconds <= 0 || heartbeatSeconds > Long.MAX_VALUE / 1000.0 )
				return "FrankenGAN ready marker is invalid; restart the compatibility watcher.";
			long heartbeatMillis = (long) ( heartbeatSeconds * 1000.0 );
			if ( !freshTimestamp( heartbeatMillis, nowMillis )
					|| !freshTimestamp( ready.lastModified(), nowMillis ) )
				return "FrankenGAN ready heartbeat is stale; restart the compatibility watcher.";
			return null;
		} catch ( IOException error ) {
			return "FrankenGAN ready marker is unreadable; restart the compatibility watcher.";
		}
	}

	private static boolean freshTimestamp( long timestampMillis, long nowMillis ) {
		long age = nowMillis - timestampMillis;
		return timestampMillis > 0 && age <= MAX_READY_MARKER_AGE_MILLIS
				&& age >= -MAX_READY_MARKER_FUTURE_MILLIS;
	}

	public static BufferedImage readReferenceImage( File file ) throws IOException {
		if ( file == null )
			throw new IOException( "No reference image was selected." );
		if ( !file.isFile() || !file.canRead() || file.length() == 0 )
			throw new IOException( "Reference image is missing, empty, or unreadable: " + file );
		if ( file.length() > MAX_REFERENCE_BYTES )
			throw new IOException( "Reference image is larger than 32 MB; crop it before loading." );

		try ( ImageInputStream input = ImageIO.createImageInputStream( file ) ) {
			if ( input == null )
				throw new IOException( "The selected file cannot be opened as an image." );
			Iterator<ImageReader> readers = ImageIO.getImageReaders( input );
			if ( !readers.hasNext() )
				throw new IOException( "The selected file is not an ImageIO-readable image." );
			ImageReader reader = readers.next();
			try {
				reader.setInput( input, true, true );
				int width = reader.getWidth( 0 );
				int height = reader.getHeight( 0 );
				long pixels = (long) width * (long) height;
				if ( width < 2 || height < 2 )
					throw new IOException( "Reference image dimensions are invalid." );
				if ( width > MAX_REFERENCE_DIMENSION || height > MAX_REFERENCE_DIMENSION
						|| pixels > MAX_REFERENCE_PIXELS )
					throw new IOException( "Reference image exceeds the safe 16-megapixel size limit; crop it before loading." );
				BufferedImage image = reader.read( 0 );
				if ( image == null )
					throw new IOException( "The selected image could not be decoded." );
				return image;
			} finally {
				reader.dispose();
			}
		}
	}

	/** Flatten alpha and exotic colour models before handing the image to BicycleGAN. */
	public static BufferedImage toEncoderRgb( BufferedImage source ) {
		BufferedImage rgb = new BufferedImage( source.getWidth(), source.getHeight(),
				BufferedImage.TYPE_3BYTE_BGR );
		Graphics2D graphics = rgb.createGraphics();
		graphics.drawImage( source, 0, 0, null );
		graphics.dispose();
		return rgb;
	}

	public static String invalidLatentReason( double[] latent, int expectedDimensions ) {
		if ( expectedDimensions != STYLE_DIMENSIONS )
			return "Reference styles require exactly 8 latent dimensions.";
		if ( latent == null )
			return "FrankenGAN encoder returned no style vector.";
		if ( latent.length != expectedDimensions )
			return "FrankenGAN encoder returned " + latent.length + " values; expected "
					+ expectedDimensions + ".";
		for ( double value : latent )
			if ( Double.isNaN( value ) || Double.isInfinite( value ) )
				return "FrankenGAN encoder returned a non-finite style value.";
		return null;
	}

	/** Validate the complete candidate before changing any element of the live vector. */
	public static void commitLatent( double[] destination, double[] candidate ) {
		if ( destination == null )
			throw new IllegalArgumentException( "Missing destination style vector." );
		String invalid = invalidLatentReason( candidate, destination.length );
		if ( invalid != null )
			throw new IllegalArgumentException( invalid );
		System.arraycopy( candidate, 0, destination, 0, destination.length );
	}

	public static double[] copyLatent( double[] latent, int expectedDimensions ) {
		String invalid = invalidLatentReason( latent, expectedDimensions );
		if ( invalid != null )
			throw new IllegalArgumentException( invalid );
		return Arrays.copyOf( latent, latent.length );
	}

	public static File roofReferenceCandidate( File blockRoot ) {
		if ( blockRoot == null )
			return null;
		return new File( blockRoot, "references" + File.separator + "roof"
				+ File.separator + "roof_reference.png" );
	}

	/** Use only the selected block's publication, and only when reference.json is READY. */
	public static File readyRoofReference( File blockRoot ) {
		File candidate = roofReferenceCandidate( blockRoot );
		if ( candidate == null )
			return null;
		File references = new File( blockRoot, "references" );
		File roofDirectory = candidate.getParentFile();
		File metadata = new File( roofDirectory, "reference.json" );
		if ( !candidate.isFile() || candidate.length() == 0 || !metadata.isFile()
				|| metadata.length() == 0 || metadata.length() > MAX_ROOF_MANIFEST_BYTES )
			return null;
		try {
			if ( Files.isSymbolicLink( references.toPath() )
					|| Files.isSymbolicLink( roofDirectory.toPath() )
					|| Files.isSymbolicLink( metadata.toPath() ) )
				return null;
			JsonNode document = readBoundedJson( metadata, MAX_ROOF_MANIFEST_BYTES );
			if ( document == null || !document.isObject()
					|| document.path( "schema_version" ).asInt( -1 ) != 1
					|| !"myProject.appearance.roof_reference".equals( document.path( "kind" ).asText() )
					|| !"READY".equals( document.path( "status" ).asText() ) )
				return null;

			JsonNode outputs = document.get( "outputs" );
			JsonNode hashes = document.get( "output_sha256" );
			if ( outputs == null || !outputs.isObject() || outputs.size() != ROOF_OUTPUTS.length
					|| hashes == null || !hashes.isObject() || hashes.size() != ROOF_OUTPUTS.length )
				return null;

			for ( String[] contract : ROOF_OUTPUTS ) {
				String key = contract[0], name = contract[1];
				JsonNode output = outputs.get( key );
				JsonNode hash = hashes.get( key );
				if ( output == null || !output.isTextual() || !name.equals( output.asText() )
						|| hash == null || !hash.isTextual() || !isLowerSha256( hash.asText() ) )
					return null;
				File published = new File( candidate.getParentFile(), name );
				if ( !published.isFile() || published.length() == 0
						|| Files.isSymbolicLink( published.toPath() )
						|| published.length() > MAX_ROOF_OUTPUT_BYTES
						|| !hash.asText().equals( sha256( published ) ) )
					return null;
			}
			return candidate;
		} catch ( IOException | NoSuchAlgorithmException e ) {
			return null;
		}
	}

	private static JsonNode readBoundedJson( File file, long maximumBytes ) throws IOException {
		try ( InputStream input = new BufferedInputStream( new FileInputStream( file ) );
				ByteArrayOutputStream output = new ByteArrayOutputStream() ) {
			byte[] buffer = new byte[8192];
			long total = 0;
			for ( int read; ( read = input.read( buffer ) ) >= 0; ) {
				total += read;
				if ( total > maximumBytes )
					throw new IOException( "JSON file exceeds its size limit." );
				output.write( buffer, 0, read );
			}
			return JSON.readTree( output.toByteArray() );
		}
	}

	private static boolean isLowerSha256( String value ) {
		if ( value == null || value.length() != 64 )
			return false;
		for ( int i = 0; i < value.length(); i++ ) {
			char character = value.charAt( i );
			if ( !( character >= '0' && character <= '9' )
					&& !( character >= 'a' && character <= 'f' ) )
				return false;
		}
		return true;
	}

	private static String sha256( File file ) throws IOException, NoSuchAlgorithmException {
		MessageDigest digest = MessageDigest.getInstance( "SHA-256" );
		try ( InputStream input = new BufferedInputStream( new FileInputStream( file ) ) ) {
			byte[] buffer = new byte[8192];
			for ( int read; ( read = input.read( buffer ) ) >= 0; )
				digest.update( buffer, 0, read );
		}
		StringBuilder out = new StringBuilder( 64 );
		for ( byte value : digest.digest() )
			out.append( String.format( "%02x", value & 0xff ) );
		return out.toString();
	}
}
